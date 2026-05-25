from __future__ import annotations

import re


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
IMAGE_MARKER_RE = re.compile(r"\[\[BOOK_CONDENSER_IMAGE:([A-Za-z0-9_-]+)\]\]")


def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    return BLANK_LINES_RE.sub("\n\n", text).strip()


def count_units(text: str) -> int:
    text = IMAGE_MARKER_RE.sub("", text)
    return len(CJK_RE.findall(text)) + len(WORD_RE.findall(text))


def strip_reasoning(text: str) -> str:
    return normalize_text(THINK_RE.sub("", text))


def paragraphs_to_html(text: str) -> str:
    import html

    blocks = [block.strip() for block in normalize_text(text).split("\n\n") if block.strip()]
    if not blocks:
        return "<p></p>"
    return "\n".join(f"<p>{html.escape(block).replace(chr(10), '<br/>')}</p>" for block in blocks)


def safe_filename_part(value: str, fallback: str = "book", max_length: int = 80) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.UNICODE).strip("._")
    if max_length > 0 and len(value) > max_length:
        value = value[:max_length].strip("._")
    return value or fallback
