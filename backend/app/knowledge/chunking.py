"""Split document text into overlapping passages for embedding (FR-05-02).

Passages follow paragraph and sentence boundaries where possible, so a retrieved chunk
reads as a complete statement (a policy sentence isn't cut in half).
"""

import re

# Small passages (a policy section or two) score higher against short spoken questions
# with MiniLM than whole pages do. Provisional; tuned with the retrieval eval in M10.
DEFAULT_MAX_CHARS = 350
DEFAULT_OVERLAP_CHARS = 80

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chunk_text(
    text: str, *, max_chars: int = DEFAULT_MAX_CHARS, overlap_chars: int = DEFAULT_OVERLAP_CHARS
) -> list[str]:
    if overlap_chars >= max_chars:
        raise ValueError("overlap must be smaller than the chunk size")

    chunks: list[str] = []
    current = ""
    for separator, unit in _units(text, max_chars):
        candidate = f"{current}{separator}{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        # Start the next chunk with the last sentence(s) of this one, so a statement that
        # spans the boundary is still found whole in one of them.
        tail = _tail(current, overlap_chars)
        current = f"{tail} {unit}" if tail and len(tail) + len(unit) + 1 <= max_chars else unit
    if current:
        chunks.append(current)
    return chunks


def _units(text: str, max_chars: int) -> list[tuple[str, str]]:
    """(separator, text) pieces no longer than `max_chars`: whole paragraphs when they fit,
    otherwise their sentences (and over-long sentences cut at spaces)."""
    units: list[tuple[str, str]] = []
    for paragraph in _paragraphs(text):
        if len(paragraph) <= max_chars:
            units.append(("\n\n", paragraph))
            continue
        pieces = [
            piece
            for sentence in _SENTENCE_END.split(paragraph)
            for piece in _hard_wrap(sentence, max_chars)
        ]
        units.extend((" " if index else "\n\n", piece) for index, piece in enumerate(pieces))
    return units


def _paragraphs(text: str) -> list[str]:
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]


def _hard_wrap(sentence: str, max_chars: int) -> list[str]:
    words, lines, line = sentence.split(" "), [], ""
    for word in words:
        while len(word) > max_chars:  # a single enormous "word" (e.g. a URL)
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{line} {word}" if line else word
        if len(candidate) <= max_chars:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _tail(chunk: str, overlap_chars: int) -> str:
    """The last whole sentences of a chunk, up to `overlap_chars`, carried into the next."""
    if overlap_chars == 0:
        return ""
    tail = ""
    for sentence in reversed(_SENTENCE_END.split(chunk.replace("\n\n", " "))):
        candidate = f"{sentence} {tail}".strip()
        if len(candidate) > overlap_chars:
            break
        tail = candidate
    return tail
