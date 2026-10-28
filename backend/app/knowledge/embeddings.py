"""Text embedders for knowledge retrieval (FR-05-02; ADR-0004).

Embedders are synchronous and CPU-bound; callers run them in a worker thread. Each has a
`name` that becomes part of the tenant's collection name, so vectors from different
embedders are never mixed in one collection.
"""

import hashlib
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Protocol

from app.core.config import Settings

if TYPE_CHECKING:
    from chromadb.api.types import EmbeddingFunction


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MiniLMEmbedder:
    """all-MiniLM-L6-v2 through ChromaDB's bundled ONNX runtime (no torch needed).

    Runs locally and costs nothing per call. The model (~80 MB) is downloaded to the user's
    cache on first use, so the first upload or question after install is slow.
    """

    name = "minilm"

    def __init__(self) -> None:
        self._function: EmbeddingFunction[list[str]] | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._function is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            self._function = DefaultEmbeddingFunction()
        return [[float(value) for value in vector] for vector in self._function(texts)]


_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    (  # noqa: SIM905  # a word list reads better than a 60-item literal
        "a an and are as at be by can do does for from has have how i if in is it its me my of "
        "on or our please the their there this to us we what when where which who will with "
        "you your"
    ).split()
)


class HashingEmbedder:
    """A deterministic bag-of-words embedding with no model to download.

    Only shared words make texts similar, so it is good enough for tests and offline demos
    but not for real paraphrased questions.
    """

    name = "hashing"

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        # Damped counts: a word repeated in a long passage shouldn't drown the rest.
        # Bare numbers ("12", "00") would make every timetable look alike; skip them.
        words = [word for word in content_words(text) if not word.isdigit()]
        for word, count in Counter(words).items():
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest) % self._dimensions] += 1 + math.log(count)
        norm = math.sqrt(sum(value * value for value in vector))
        # An all-stopword text gets a constant vector rather than zeros (cosine needs a norm).
        if norm == 0:
            return [1 / math.sqrt(self._dimensions)] * self._dimensions
        return [value / norm for value in vector]


def content_words(text: str) -> list[str]:
    """Lower-cased, lightly stemmed words of a text, without common filler words."""
    return [_stem(word) for word in _WORD.findall(text.lower()) if word not in _STOPWORDS]


def _stem(word: str) -> str:
    # Just enough for "bookings", "booked" and "booking", or "cancellation" and "cancel",
    # to match.
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"  # "policies" -> "policy"
    for suffix in ("ations", "ation", "ings", "ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if word[-1] == word[-2]:  # "cancell" -> "cancel"
                word = word[:-1]
            break
    return word


def create_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    return MiniLMEmbedder()
