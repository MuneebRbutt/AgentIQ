"""Sentiment classifiers for customer messages (FR-06-01; ADR-0006).

Two implementations behind `SentimentClassifier`:

- `OnnxSentimentClassifier`: a three-class (negative / neutral / positive) transformer from
  the Hugging Face Hub, run with the ONNX runtime that ChromaDB already ships (no torch).
  The default is a DistilBERT student model; its files (~135 MB) are downloaded once into
  `SENTIMENT_MODEL_PATH`. Free per call, about 20-60 ms on a laptop CPU.
- `LexiconClassifier`: a small word list with negation and intensifiers. Deterministic and
  instant; used in tests, offline, and whenever the model can't be loaded (NFR04-01).

Classifiers are synchronous and CPU-bound; callers run them in a worker thread.
"""

import json
import math
import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.sentiment_lexicon import (
    INTENSIFIERS,
    NEGATORS,
    PHRASES,
    WORDS,
)

if TYPE_CHECKING:
    import onnxruntime
    from tokenizers import Tokenizer

log = get_logger(__name__)

MAX_TOKENS = 256
DOWNLOAD_TIMEOUT_SECONDS = 120.0


class SentimentLabel(StrEnum):
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"


@dataclass(frozen=True, slots=True)
class SentimentScore:
    label: SentimentLabel
    confidence: float  # probability of `label`, 0-1
    scores: dict[str, float]  # probability of every label
    model: str  # which classifier produced it


class SentimentClassifier(Protocol):
    @property
    def name(self) -> str: ...

    def classify(self, text: str) -> SentimentScore: ...


def _score(probabilities: dict[SentimentLabel, float], model: str) -> SentimentScore:
    label = max(probabilities, key=lambda key: probabilities[key])
    return SentimentScore(
        label=label,
        confidence=round(probabilities[label], 4),
        scores={key.value: round(value, 4) for key, value in probabilities.items()},
        model=model,
    )


# --- Lexicon --------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?|!", re.IGNORECASE)
NEUTRAL_BAND = 1.0  # |score| below this is neutral
NEGATION_WEIGHT = -0.75  # "not good" is mildly negative, not as strong as "bad"
NEGATION_REACH = 3  # a negator affects the next few words
INTENSITY = 1.5
EXCLAMATION = 0.3


class LexiconClassifier:
    """Sums word weights ("furious" -3, "thanks" +1), flipping them after a negator and
    strengthening them after "very" or in capitals; '!' adds emphasis. Phrases such as
    "third time" or "fed up" carry complaint context a word list would miss (FR-06-02)."""

    name = "lexicon"

    def classify(self, text: str) -> SentimentScore:
        score = self.polarity(text)
        magnitude = abs(score)
        if magnitude < NEUTRAL_BAND:
            neutral = 0.9 - 0.3 * magnitude
            rest = (1 - neutral) / 2
            negative, positive = (
                (rest + 0.1 * magnitude, rest - 0.1 * magnitude)
                if score < 0
                else (rest - 0.1 * magnitude, rest + 0.1 * magnitude)
            )
            return _score(
                {
                    SentimentLabel.NEGATIVE: negative,
                    SentimentLabel.NEUTRAL: neutral,
                    SentimentLabel.POSITIVE: positive,
                },
                self.name,
            )
        # 0.5 at the edge of the neutral band, ~0.6 for one complaint word ("dirty"), ~0.72
        # at 3, ~0.9 for an angry sentence: one mild word alone shouldn't escalate.
        confidence = 0.5 + 0.5 * (1 - math.exp(-(magnitude - NEUTRAL_BAND) / 2.5))
        polar, other = (
            (SentimentLabel.NEGATIVE, SentimentLabel.POSITIVE)
            if score < 0
            else (SentimentLabel.POSITIVE, SentimentLabel.NEGATIVE)
        )
        remaining = 1 - confidence
        return _score(
            {polar: confidence, SentimentLabel.NEUTRAL: remaining * 0.8, other: remaining * 0.2},
            self.name,
        )

    @staticmethod
    def polarity(text: str) -> float:
        lowered = " ".join(text.lower().split())
        score = sum(weight for phrase, weight in PHRASES.items() if phrase in lowered)
        negate_for = 0
        boost = 1.0
        exclamations = 0
        for piece in _TOKEN.findall(text):
            if piece == "!":
                exclamations += 1
                continue
            word = piece.lower()
            if word in NEGATORS or word.endswith("n't"):
                negate_for = NEGATION_REACH
                continue
            if word in INTENSIFIERS:
                boost = INTENSITY
                continue
            weight = WORDS.get(word, 0.0)
            if weight:
                if piece.isupper() and len(piece) >= 3:
                    weight *= INTENSITY
                weight *= boost
                if negate_for:
                    weight *= NEGATION_WEIGHT
                score += weight
                boost = 1.0
            negate_for = max(negate_for - 1, 0)
        if score and exclamations:
            score += math.copysign(min(exclamations, 3) * EXCLAMATION, score)
        return score


# --- ONNX transformer -----------------------------------------------------------------


def _normalise_label(raw: str) -> SentimentLabel | None:
    lowered = raw.lower()
    for label in SentimentLabel:
        if lowered.startswith(label.value[:3]):
            return label
    return None


class OnnxSentimentClassifier:
    """A sequence-classification model exported to ONNX, with its `tokenizer.json`."""

    def __init__(
        self,
        session: "onnxruntime.InferenceSession",
        tokenizer: "Tokenizer",
        labels: dict[int, SentimentLabel],
        name: str,
    ) -> None:
        if set(labels.values()) != set(SentimentLabel):
            raise ValueError("the model must have negative, neutral and positive labels")
        self._session = session
        self._tokenizer = tokenizer
        self._labels = labels
        self._inputs = [model_input.name for model_input in session.get_inputs()]
        self.name = name

    @classmethod
    def from_directory(
        cls, directory: Path, model_file: str, name: str
    ) -> "OnnxSentimentClassifier":
        import onnxruntime
        from tokenizers import Tokenizer

        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        labels = {
            int(index): label
            for index, raw in config["id2label"].items()
            if (label := _normalise_label(str(raw))) is not None
        }
        tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        tokenizer.no_padding()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2  # leave cores for the API and the embedder
        session = onnxruntime.InferenceSession(
            str(directory / model_file), options, providers=["CPUExecutionProvider"]
        )
        return cls(session, tokenizer, labels, name)

    def classify(self, text: str) -> SentimentScore:
        import numpy as np

        encoding = self._tokenizer.encode(text)
        ids = np.array([encoding.ids], dtype=np.int64)
        feeds: dict[str, Any] = {}
        for name in self._inputs:
            if name == "input_ids":
                feeds[name] = ids
            elif name == "attention_mask":
                feeds[name] = np.array([encoding.attention_mask], dtype=np.int64)
            elif name == "token_type_ids":
                feeds[name] = np.zeros_like(ids)
        logits = np.asarray(self._session.run(None, feeds)[0], dtype=np.float64)[0]
        exp = np.exp(logits - logits.max())
        probabilities = exp / exp.sum()
        return _score(
            {label: float(probabilities[index]) for index, label in self._labels.items()},
            self.name,
        )


def download_model(
    repo: str,
    model_file: str,
    directory: Path,
    *,
    base_url: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Fetch the model's files from the Hub once. Each file is written under a temporary
    name and renamed, so an interrupted download is never mistaken for a complete one."""
    directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        follow_redirects=True,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        transport=transport,
    ) as client:
        for file in ("config.json", "tokenizer.json", model_file):
            target = directory / file
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            log.info("sentiment_model_download", repo=repo, file=file)
            with client.stream("GET", f"/{repo}/resolve/main/{file}") as response:
                response.raise_for_status()
                with partial.open("wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
            partial.replace(target)


class ModelSentimentClassifier:
    """The configured transformer, loaded (and downloaded) on first use. If it can't be
    loaded, every message is classified by the lexicon instead and the reason is logged;
    `name` then says "lexicon", so stored records show which classifier really ran."""

    def __init__(
        self,
        repo: str,
        model_file: str,
        directory: Path,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._repo = repo
        self._model_file = model_file
        self._directory = directory
        self._base_url = base_url
        self._transport = transport
        self._model: SentimentClassifier | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._model.name if self._model is not None else self._repo

    def classify(self, text: str) -> SentimentScore:
        return self.load().classify(text)

    def load(self) -> SentimentClassifier:
        with self._lock:  # one download even if several turns arrive at once
            if self._model is None:
                try:
                    download_model(
                        self._repo,
                        self._model_file,
                        self._directory,
                        base_url=self._base_url,
                        transport=self._transport,
                    )
                    self._model = OnnxSentimentClassifier.from_directory(
                        self._directory, self._model_file, self._repo
                    )
                except Exception as exc:
                    log.warning(
                        "sentiment_model_unavailable",
                        repo=self._repo,
                        error=f"{type(exc).__name__}: {str(exc)[:200]}",
                    )
                    self._model = LexiconClassifier()
            return self._model


def create_sentiment_classifier(settings: Settings) -> SentimentClassifier:
    if settings.sentiment_provider == "lexicon":
        return LexiconClassifier()
    directory = settings.sentiment_model_directory / settings.sentiment_model.replace("/", "--")
    return ModelSentimentClassifier(
        settings.sentiment_model,
        settings.sentiment_model_file,
        directory,
        settings.sentiment_model_base_url,
    )
