"""Sentiment classification and the escalation rules (FR-06-01…04, TC-06, UC-06)."""

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from app import cli
from app.agents.contracts import AgentContext, AgentStatus
from app.agents.sentiment.agent import (
    REASON_KEYWORD,
    REASON_NEGATIVE,
    REASON_REPEATED,
    SentimentAgent,
    SentimentSubtask,
    assess,
    matched_keywords,
)
from app.core.config import Settings
from app.profiles.models import BusinessProfile
from app.profiles.schema import EscalationRules
from app.providers.notifications import LogNotifier, Notice, mask_contact
from app.providers.sentiment import (
    LexiconClassifier,
    ModelSentimentClassifier,
    OnnxSentimentClassifier,
    SentimentLabel,
    SentimentScore,
    create_sentiment_classifier,
    download_model,
)

FURIOUS = "This is the third time my booking has been wrong, I'm furious."  # TC-06 step 1
NEUTRAL = "What time do you close today?"  # TC-06 step 3
RULES = EscalationRules(keywords=["manager", "food poisoning"])
lexicon = LexiconClassifier()


# --- Lexicon ----------------------------------------------------------------------------


def test_tc06_a_furious_message_is_strongly_negative() -> None:
    score = lexicon.classify(FURIOUS)
    assert score.label is SentimentLabel.NEGATIVE
    assert score.confidence >= 0.9
    assert score.model == "lexicon"


def test_tc06_a_plain_question_is_neutral() -> None:
    score = lexicon.classify(NEUTRAL)
    assert score.label is SentimentLabel.NEUTRAL
    assert score.confidence == 0.9


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Thank you so much, that was really helpful!", SentimentLabel.POSITIVE),
        ("The room was dirty", SentimentLabel.NEGATIVE),
        ("The food wasn't good", SentimentLabel.NEGATIVE),  # negation flips "good"
        ("Not bad at all", SentimentLabel.POSITIVE),
        ("Ali Raza", SentimentLabel.NEUTRAL),
        ("Yes please", SentimentLabel.NEUTRAL),
    ],
)
def test_lexicon_labels(text: str, label: SentimentLabel) -> None:
    assert lexicon.classify(text).label is label


def test_emphasis_makes_a_message_stronger() -> None:
    plain = LexiconClassifier.polarity("this is unacceptable")
    assert LexiconClassifier.polarity("this is UNACCEPTABLE") < plain
    assert LexiconClassifier.polarity("this is unacceptable!!!") < plain
    assert LexiconClassifier.polarity("this is totally unacceptable") < plain
    assert LexiconClassifier.polarity("this is not unacceptable") > 0


def test_one_mild_complaint_word_is_negative_but_not_strong() -> None:
    score = lexicon.classify("The table was dirty")
    assert score.label is SentimentLabel.NEGATIVE
    assert 0.55 <= score.confidence < 0.7  # counts, but alone won't escalate at 0.7


def test_scores_are_probabilities() -> None:
    for text in (FURIOUS, NEUTRAL, "lovely", "a bit late"):
        score = lexicon.classify(text)
        assert set(score.scores) == {"negative", "neutral", "positive"}
        assert sum(score.scores.values()) == pytest.approx(1, abs=0.001)
        assert score.scores[score.label.value] == score.confidence


# --- Escalation rules -------------------------------------------------------------------


def _score(label: SentimentLabel, confidence: float) -> SentimentScore:
    return SentimentScore(label, confidence, {label.value: confidence}, "test")


def test_tc06_a_confident_negative_message_escalates() -> None:
    result = assess(_score(SentimentLabel.NEGATIVE, 0.95), FURIOUS, 0, RULES, 0.55)
    assert result.reasons == [REASON_NEGATIVE]
    assert result.complaint and result.negative and result.negative_streak == 1


def test_tc06_a_neutral_message_does_not_escalate() -> None:
    result = assess(_score(SentimentLabel.NEUTRAL, 0.9), NEUTRAL, 1, RULES, 0.55)
    assert result.reasons == []
    assert not result.complaint and result.negative_streak == 0  # the streak is broken


def test_a_keyword_escalates_whatever_the_sentiment() -> None:
    result = assess(_score(SentimentLabel.NEUTRAL, 0.9), "Get me the Manager.", 0, RULES, 0.55)
    assert result.reasons == [REASON_KEYWORD]
    assert result.keywords == ["manager"] and result.complaint


def test_keywords_match_whole_words_and_phrases() -> None:
    keywords = ["manager", "food poisoning"]
    assert matched_keywords("I think it's food\n poisoning!", keywords) == ["food poisoning"]
    assert matched_keywords("the food was fine, no poisoning", keywords) == []
    assert matched_keywords("our managers are great", keywords) == []
    assert matched_keywords("MANAGER, now", keywords) == ["manager"]


def test_mild_negative_messages_in_a_row_escalate() -> None:
    mild = _score(SentimentLabel.NEGATIVE, 0.6)  # below the 0.7 threshold
    first = assess(mild, "a bit cold", 0, RULES, 0.55)
    second = assess(mild, "still cold", first.negative_streak, RULES, 0.55)
    third = assess(mild, "cold again", second.negative_streak, RULES, 0.55)
    assert (first.reasons, second.reasons) == ([], [])
    assert third.reasons == [REASON_REPEATED] and third.negative_streak == 3


def test_low_confidence_never_escalates_on_sentiment_but_is_flagged() -> None:
    unsure = _score(SentimentLabel.NEGATIVE, 0.5)
    result = assess(unsure, "hmm", 2, RULES, 0.55)
    assert result.low_confidence and not result.negative
    assert result.reasons == [] and result.negative_streak == 0
    # A keyword still counts: it doesn't depend on the model.
    assert assess(unsure, "manager", 2, RULES, 0.55).reasons == [REASON_KEYWORD]


def test_threshold_and_streak_come_from_the_profile() -> None:
    strict = EscalationRules(negative_sentiment_threshold=0.95, negative_turns_in_a_row=2)
    score = _score(SentimentLabel.NEGATIVE, 0.9)
    assert assess(score, "x", 0, strict, 0.55).reasons == []
    assert assess(score, "x", 1, strict, 0.55).reasons == [REASON_REPEATED]


def _context(tenant: uuid.UUID | None = None) -> AgentContext:
    owner = uuid.uuid4()
    profile = BusinessProfile(id=uuid.uuid4(), tenant_id=owner, config={}, version=1)
    return AgentContext(tenant_id=tenant or owner, profile=profile)


async def test_agent_reports_the_reading_and_the_decision() -> None:
    agent = SentimentAgent(lexicon, min_confidence=0.55)
    result = await agent.run(_context(), SentimentSubtask(text=FURIOUS, escalation=RULES))
    assert result.agent == "sentiment" and result.status is AgentStatus.COMPLETED
    assert result.data["label"] == "negative" and result.data["escalate"] is True
    assert result.data["reasons"] == [REASON_NEGATIVE]
    assert "negative" in result.message and "Escalate" in result.message

    calm = await agent.run(_context(), SentimentSubtask(text=NEUTRAL, escalation=RULES))
    assert calm.data["escalate"] is False and calm.data["complaint"] is False


async def test_agent_refuses_a_profile_of_another_tenant() -> None:
    agent = SentimentAgent(lexicon, min_confidence=0.55)
    with pytest.raises(ValueError, match="tenant"):
        await agent.run(
            _context(tenant=uuid.uuid4()), SentimentSubtask(text="hi", escalation=RULES)
        )


# --- Transformer through ONNX -----------------------------------------------------------


@dataclass
class _Input:
    name: str


@dataclass
class FakeSession:
    logits: list[float]
    inputs: list[str] = field(default_factory=lambda: ["input_ids", "attention_mask"])
    feeds: list[dict[str, Any]] = field(default_factory=list)

    def get_inputs(self) -> list[_Input]:
        return [_Input(name) for name in self.inputs]

    def run(self, outputs: None, feeds: dict[str, Any]) -> list[Any]:
        self.feeds.append(feeds)
        return [np.array([self.logits])]


@dataclass
class _Encoding:
    ids: list[int]
    attention_mask: list[int]


class FakeTokenizer:
    def encode(self, text: str) -> _Encoding:
        return _Encoding([101, *range(len(text.split())), 102], [1] * (len(text.split()) + 2))


LABELS = {0: SentimentLabel.POSITIVE, 1: SentimentLabel.NEUTRAL, 2: SentimentLabel.NEGATIVE}


def test_onnx_classifier_maps_logits_to_labels() -> None:
    session = FakeSession(logits=[-1.0, 0.0, 3.0])
    model = OnnxSentimentClassifier(session, FakeTokenizer(), LABELS, "distilbert")  # type: ignore[arg-type]
    score = model.classify("so angry right now")
    assert score.label is SentimentLabel.NEGATIVE
    assert score.confidence == pytest.approx(0.936, abs=0.001)
    assert sum(score.scores.values()) == pytest.approx(1, abs=0.001)
    assert score.model == "distilbert"
    [feeds] = session.feeds
    assert feeds["input_ids"].tolist() == [[101, 0, 1, 2, 3, 102]]
    assert feeds["attention_mask"].dtype == np.int64


def test_onnx_classifier_fills_token_type_ids_for_models_that_want_them() -> None:
    session = FakeSession(logits=[0.0, 2.0, 0.0], inputs=["input_ids", "token_type_ids"])
    model = OnnxSentimentClassifier(session, FakeTokenizer(), LABELS, "bert")  # type: ignore[arg-type]
    assert model.classify("ok").label is SentimentLabel.NEUTRAL
    assert session.feeds[0]["token_type_ids"].tolist() == [[0, 0, 0]]
    assert "attention_mask" not in session.feeds[0]


def test_onnx_classifier_needs_three_labels() -> None:
    binary = {0: SentimentLabel.NEGATIVE, 1: SentimentLabel.POSITIVE}
    with pytest.raises(ValueError, match="neutral"):
        OnnxSentimentClassifier(FakeSession([0, 0]), FakeTokenizer(), binary, "sst2")  # type: ignore[arg-type]


def _hub(files: dict[str, bytes], requests: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        name = request.url.path.split("/resolve/main/", 1)[1]
        if name in files:
            return httpx.Response(200, content=files[name])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def test_download_fetches_each_file_once(tmp_path: Path) -> None:
    files = {"config.json": b"{}", "tokenizer.json": b"{}", "onnx/model.onnx": b"weights"}
    requests: list[str] = []
    for _ in range(2):
        download_model(
            "org/model",
            "onnx/model.onnx",
            tmp_path,
            base_url="https://hub.test",
            transport=_hub(files, requests),
        )
    assert (tmp_path / "onnx" / "model.onnx").read_bytes() == b"weights"
    assert requests == [
        "/org/model/resolve/main/config.json",
        "/org/model/resolve/main/tokenizer.json",
        "/org/model/resolve/main/onnx/model.onnx",
    ]


def test_an_interrupted_download_leaves_no_file_behind(tmp_path: Path) -> None:
    files = {"config.json": b"{}", "tokenizer.json": b"{}"}  # the weights are missing
    with pytest.raises(httpx.HTTPStatusError):
        download_model(
            "org/model",
            "onnx/model.onnx",
            tmp_path,
            base_url="https://hub.test",
            transport=_hub(files, []),
        )
    assert not (tmp_path / "onnx" / "model.onnx").exists()
    assert (tmp_path / "config.json").exists()  # finished files are kept for next time


def test_model_falls_back_to_the_lexicon_once_when_it_cannot_load(tmp_path: Path) -> None:
    requests: list[str] = []
    model = ModelSentimentClassifier(
        "org/model", "onnx/model.onnx", tmp_path, "https://hub.test", _hub({}, requests)
    )
    assert model.name == "org/model"
    first = model.classify(FURIOUS)
    second = model.classify(NEUTRAL)
    assert (first.label, second.label) == (SentimentLabel.NEGATIVE, SentimentLabel.NEUTRAL)
    assert first.model == "lexicon" and model.name == "lexicon"
    assert len(requests) == 1  # no retry on every message


def test_create_sentiment_classifier(tmp_path: Path) -> None:
    lexicon_settings = Settings(_env_file=None, sentiment_provider="lexicon")
    assert isinstance(create_sentiment_classifier(lexicon_settings), LexiconClassifier)
    model = create_sentiment_classifier(
        Settings(_env_file=None, sentiment_model_path=str(tmp_path), sentiment_model="a/b")
    )
    assert isinstance(model, ModelSentimentClassifier)
    assert model.name == "a/b"


def test_the_real_model_when_it_is_downloaded() -> None:
    """Runs where `python -m app.cli check-sentiment` has downloaded the model."""
    settings = Settings(_env_file=None)
    directory = settings.sentiment_model_directory / settings.sentiment_model.replace("/", "--")
    if not (directory / settings.sentiment_model_file).exists():
        pytest.skip("sentiment model not downloaded")
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    assert len(config["id2label"]) == 3
    model = OnnxSentimentClassifier.from_directory(
        directory, settings.sentiment_model_file, settings.sentiment_model
    )
    assert model.classify(FURIOUS).label is SentimentLabel.NEGATIVE
    assert model.classify("Thank you, that was wonderful!").label is SentimentLabel.POSITIVE


# --- Notifications ----------------------------------------------------------------------


def test_contacts_are_masked_in_logs() -> None:
    assert mask_contact("duty.manager@grand.example.com") == "d***@grand.example.com"
    assert mask_contact("+92 300 1234567") == "***567"
    assert mask_contact(None) is None


async def test_log_notifier_records_that_it_only_logged() -> None:
    notice = Notice(subject="s", body="b", phone="+92 300 1234567")
    assert await LogNotifier().send(notice) == "logged"


def test_check_sentiment_command(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(_env_file=None, sentiment_provider="lexicon")
    cli.check_sentiment(settings, cli.SENTIMENT_EXAMPLES, LexiconClassifier())
    out = capsys.readouterr().out
    assert "negative 0.94" in out and "neutral 0.90" in out and "positive" in out
    assert out.rstrip().endswith("Classifier: lexicon")
