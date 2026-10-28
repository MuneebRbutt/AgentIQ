"""Word weights for `LexiconClassifier` (negative below zero, positive above).

Kept short and aimed at customer-service messages: bookings, food, rooms, appointments.
Add words here, not in code; `tests/unit/test_sentiment.py` checks the TC-06 messages.
"""

WORDS: dict[str, float] = {
    # Strong anger or disgust
    "furious": -3.0,
    "livid": -3.0,
    "outraged": -3.0,
    "disgusting": -3.0,
    "disgusted": -3.0,
    "unacceptable": -3.0,
    "worst": -3.0,
    "pathetic": -3.0,
    "scam": -3.0,
    "appalling": -3.0,
    "horrible": -2.5,
    "terrible": -2.5,
    "awful": -2.5,
    "ridiculous": -2.5,
    "useless": -2.5,
    "hate": -2.5,
    "angry": -2.5,
    "lied": -2.5,
    "liar": -2.5,
    "incompetent": -2.5,
    "shocking": -2.5,
    # Clear dissatisfaction
    "disappointed": -2.0,
    "disappointing": -2.0,
    "upset": -2.0,
    "annoyed": -2.0,
    "annoying": -2.0,
    "frustrated": -2.0,
    "frustrating": -2.0,
    "unhappy": -2.0,
    "rude": -2.0,
    "dirty": -2.0,
    "filthy": -2.5,
    "nonsense": -2.0,
    "complain": -1.5,
    "complaint": -1.5,
    "bad": -1.5,
    "wrong": -1.5,
    "poor": -1.5,
    "broken": -1.5,
    "mistake": -1.5,
    "sick": -1.5,
    "cold": -0.5,
    "late": -1.0,
    "problem": -1.0,
    "issue": -1.0,
    "refund": -1.0,
    "cancelled": -0.5,
    "waited": -1.0,
    "sorry": -0.5,
    # Positive
    "thanks": 1.0,
    "thank": 1.0,
    "appreciate": 1.5,
    "good": 1.5,
    "nice": 1.5,
    "happy": 1.5,
    "pleased": 1.5,
    "glad": 1.5,
    "great": 2.0,
    "perfect": 2.0,
    "love": 2.0,
    "lovely": 2.0,
    "helpful": 2.0,
    "delicious": 2.0,
    "clean": 1.0,
    "excellent": 2.5,
    "amazing": 2.5,
    "wonderful": 2.5,
    "fantastic": 2.5,
    "awesome": 2.5,
}

# Multi-word signals of a complaint or of repeated failure (FR-06-02).
PHRASES: dict[str, float] = {
    "third time": -2.0,
    "second time": -1.5,
    "again and again": -2.0,
    "every time": -1.0,
    "fed up": -2.5,
    "sick of": -2.5,
    "still waiting": -2.0,
    "still not": -1.5,
    "never again": -2.5,
    "food poisoning": -3.0,
    "waste of": -2.0,
    "speak to a manager": -1.5,
    "not acceptable": -1.5,
}

NEGATORS = frozenset({"not", "no", "never", "without", "hardly", "barely", "nothing"})

INTENSIFIERS = frozenset(
    {"very", "really", "so", "extremely", "absolutely", "totally", "completely", "too"}
)
