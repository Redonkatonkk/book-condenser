from __future__ import annotations

import re
import posixpath
from pathlib import Path

from bs4 import NavigableString
from bs4 import BeautifulSoup
from charset_normalizer import from_path
from ebooklib import ITEM_DOCUMENT, ITEM_IMAGE, epub
from pypdf import PdfReader

from app.schemas import Chapter, ImageAsset, IntegrityReport, ParsedBook
from app.text_utils import count_units, normalize_text


HEADING_RE = re.compile(
    r"^\s*("
    r"第[0-9一二三四五六七八九十百千万两〇零]+[章节回卷部篇][^\n]{0,50}"
    r"|Chapter\s+[0-9IVXLCDM]+[^\n]{0,60}"
    r"|CHAPTER\s+[0-9IVXLCDM]+[^\n]{0,60}"
    r"|[0-9]{1,3}[.)、]\s+\S[^\n]{0,50}"
    r")\s*$",
    re.IGNORECASE,
)


def parse_book(path: Path, original_filename: str) -> ParsedBook:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return parse_epub(path, original_filename)
    if suffix == ".pdf":
        return parse_pdf(path, original_filename)
    if suffix == ".txt":
        return parse_txt(path, original_filename)
    raise ValueError("仅支持 EPUB、PDF、TXT 文件。")


def parse_txt(path: Path, original_filename: str) -> ParsedBook:
    result = from_path(path).best()
    if result is None:
        raise ValueError("无法识别 TXT 文件编码。")
    text = normalize_text(str(result))
    title = Path(original_filename).stem
    chapters = split_text_into_chapters(text, title)
    integrity = build_integrity_report("TXT", chapters)
    return ParsedBook(title=title, author="", chapters=chapters, integrity=integrity)


def parse_pdf(path: Path, original_filename: str) -> ParsedBook:
    reader = PdfReader(str(path))
    parts: list[str] = []
    warnings: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            warnings.append(f"第 {index} 页文字提取失败：{exc}")
            page_text = ""
        if page_text.strip():
            parts.append(page_text)
    text = normalize_text("\n\n".join(parts))
    title = (reader.metadata.title if reader.metadata and reader.metadata.title else "") or Path(
        original_filename
    ).stem
    author = (reader.metadata.author if reader.metadata and reader.metadata.author else "") or ""
    chapters = split_text_into_chapters(text, title)
    integrity = build_integrity_report("PDF", chapters, warnings)
    if len(reader.pages) == 0:
        integrity.is_complete = False
        integrity.warnings.append("PDF 没有可读取页面。")
    return ParsedBook(title=title, author=author, chapters=chapters, integrity=integrity)


def parse_epub(path: Path, original_filename: str) -> ParsedBook:
    book = epub.read_epub(str(path))
    title = _first_metadata_value(book.get_metadata("DC", "title")) or Path(original_filename).stem
    author = _first_metadata_value(book.get_metadata("DC", "creator")) or ""
    chapters: list[Chapter] = []
    images: list[ImageAsset] = []
    image_marker_by_href: dict[str, str] = {}
    warnings: list[str] = []
    image_items = {
        _normalize_epub_href(item.file_name): item
        for item in book.get_items_of_type(ITEM_IMAGE)
        if item.file_name
    }

    documents = _epub_documents_in_reading_order(book)
    for item in documents:
        soup = BeautifulSoup(item.get_content(), "xml")
        for removable in soup(["script", "style"]):
            removable.decompose()
        _replace_images_with_markers(
            soup,
            item.file_name,
            image_items,
            image_marker_by_href,
            images,
        )
        heading = soup.find(["h1", "h2", "h3", "title"])
        item_title = normalize_text(heading.get_text(" ", strip=True)) if heading else ""
        text = normalize_text(soup.get_text("\n"))
        if not text or count_units(text) < 10:
            continue
        title_for_chapter = item_title or f"章节 {len(chapters) + 1}"
        chapters.append(
            Chapter(
                id=f"ch-{len(chapters) + 1}",
                title=title_for_chapter,
                text=text,
                order=len(chapters),
                original_count=count_units(text),
            )
        )

    if not documents:
        warnings.append("EPUB 未发现文档项目。")
    if len(chapters) <= 1:
        warnings.append("EPUB 章节结构较弱，可能只识别到一个正文单元。")

    integrity = build_integrity_report("EPUB", chapters, warnings)
    return ParsedBook(title=title, author=author, chapters=chapters, integrity=integrity, images=images)


def split_text_into_chapters(text: str, book_title: str) -> list[Chapter]:
    text = normalize_text(text)
    if not text:
        return []

    lines = text.split("\n")
    offsets: list[int] = []
    position = 0
    for line in lines:
        stripped = line.strip()
        if _looks_like_heading(stripped):
            offsets.append(position)
        position += len(line) + 1

    offsets = _dedupe_close_offsets(offsets)
    if len(offsets) < 2:
        return [
            Chapter(
                id="ch-1",
                title=book_title or "正文",
                text=text,
                order=0,
                original_count=count_units(text),
            )
        ]

    if offsets[0] > 0:
        offsets = [0] + offsets
    offsets.append(len(text))

    chapters: list[Chapter] = []
    for index in range(len(offsets) - 1):
        block = normalize_text(text[offsets[index] : offsets[index + 1]])
        if not block:
            continue
        title = _chapter_title_from_block(block, index, book_title)
        chapters.append(
            Chapter(
                id=f"ch-{len(chapters) + 1}",
                title=title,
                text=block,
                order=len(chapters),
                original_count=count_units(block),
            )
        )
    return chapters


def build_integrity_report(
    file_type: str, chapters: list[Chapter], warnings: list[str] | None = None
) -> IntegrityReport:
    warnings = list(warnings or [])
    total_count = sum(chapter.original_count for chapter in chapters)
    empty_chapter_count = sum(1 for chapter in chapters if chapter.original_count == 0)
    if not chapters:
        warnings.append("没有识别到可浓缩正文。")
    if len(chapters) == 1:
        warnings.append("只识别到一个章节，可能是原书缺少清晰章节标题或解析受限。")
    if empty_chapter_count:
        warnings.append(f"有 {empty_chapter_count} 个章节为空。")
    is_complete = bool(chapters) and total_count > 0 and empty_chapter_count == 0
    return IntegrityReport(
        file_type=file_type,
        is_complete=is_complete,
        warnings=warnings,
        chapter_count=len(chapters),
        total_count=total_count,
        empty_chapter_count=empty_chapter_count,
    )


def _looks_like_heading(line: str) -> bool:
    if not line or len(line) > 80:
        return False
    return bool(HEADING_RE.match(line))


def _dedupe_close_offsets(offsets: list[int]) -> list[int]:
    deduped: list[int] = []
    for offset in offsets:
        if deduped and offset - deduped[-1] < 4:
            continue
        deduped.append(offset)
    return deduped


def _chapter_title_from_block(block: str, index: int, book_title: str) -> str:
    first_line = next((line.strip() for line in block.split("\n") if line.strip()), "")
    if _looks_like_heading(first_line):
        return first_line[:80]
    if index == 0 and book_title:
        return "前言 / 开篇"
    return f"章节 {index + 1}"


def _first_metadata_value(values: list[tuple[str, dict[str, str]]]) -> str:
    if not values:
        return ""
    return values[0][0] or ""


def _replace_images_with_markers(
    soup: BeautifulSoup,
    document_file_name: str,
    image_items: dict[str, epub.EpubItem],
    image_marker_by_href: dict[str, str],
    images: list[ImageAsset],
) -> None:
    for img in soup.find_all("img"):
        src = (img.get("src") or img.get("{http://www.w3.org/1999/xlink}href") or "").strip()
        if not src:
            img.decompose()
            continue
        href = _resolve_epub_href(document_file_name, src)
        item = image_items.get(href)
        if item is None:
            img.decompose()
            continue
        marker_id = image_marker_by_href.get(href)
        if marker_id is None:
            marker_id = f"img-{len(images) + 1}"
            image_marker_by_href[href] = marker_id
            suffix = Path(item.file_name).suffix or ".img"
            images.append(
                ImageAsset(
                    id=marker_id,
                    filename=f"{marker_id}{suffix}",
                    media_type=item.media_type or "application/octet-stream",
                    content=item.get_content(),
                    alt=normalize_text(img.get("alt", "")),
                )
            )
        img.replace_with(NavigableString(f"\n\n[[BOOK_CONDENSER_IMAGE:{marker_id}]]\n\n"))


def _resolve_epub_href(document_file_name: str, src: str) -> str:
    src = src.split("#", 1)[0].split("?", 1)[0]
    if not src:
        return ""
    base = posixpath.dirname(document_file_name or "")
    return _normalize_epub_href(posixpath.normpath(posixpath.join(base, src)))


def _normalize_epub_href(href: str) -> str:
    return posixpath.normpath(href).lstrip("./")


def _epub_documents_in_reading_order(book: epub.EpubBook) -> list:
    item_by_id = {
        item.get_id(): item for item in book.get_items_of_type(ITEM_DOCUMENT) if item.get_id()
    }
    ordered = []
    seen = set()
    for entry in book.spine:
        item_id = entry[0] if isinstance(entry, tuple) else entry
        if item_id == "nav":
            seen.add(item_id)
            continue
        if item_id in seen:
            continue
        item = item_by_id.get(item_id)
        if item is None:
            continue
        ordered.append(item)
        seen.add(item_id)
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        item_id = item.get_id()
        if item_id == "nav":
            continue
        if item_id in seen:
            continue
        ordered.append(item)
    return ordered
