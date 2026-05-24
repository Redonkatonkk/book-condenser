from __future__ import annotations

import html
from pathlib import Path

from ebooklib import epub

from app.text_utils import IMAGE_MARKER_RE, normalize_text, safe_filename_part


def write_condensed_epub(
    output_path: Path,
    *,
    identifier: str,
    title: str,
    author: str,
    chapters: list[dict],
    images: list[dict] | None = None,
) -> Path:
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title(f"{title} - 浓缩版")
    book.set_language("zh")
    if author:
        book.add_author(author)

    image_by_id = {image["id"]: image for image in images or []}
    referenced_image_ids = _referenced_image_ids(chapters)
    for image_id in sorted(referenced_image_ids):
        image = image_by_id.get(image_id)
        if not image:
            continue
        book.add_item(
            epub.EpubItem(
                uid=image_id,
                file_name=f"images/{image['filename']}",
                media_type=image["media_type"],
                content=image["content"],
            )
        )

    epub_chapters = []
    for index, chapter in enumerate(chapters, start=1):
        chapter_title = chapter["title"] or f"章节 {index}"
        file_name = f"{index:04d}_{safe_filename_part(chapter_title, f'chapter_{index}')}.xhtml"
        item = epub.EpubHtml(title=chapter_title, file_name=file_name, lang="zh")
        item.content = (
            f"<h1>{html.escape(chapter_title)}</h1>"
            f"{content_to_html(chapter['content'], image_by_id)}"
        )
        book.add_item(item)
        epub_chapters.append(item)

    book.toc = tuple(epub_chapters)
    book.spine = ["nav", *epub_chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_path), book)
    return output_path


def content_to_html(text: str, image_by_id: dict[str, dict]) -> str:
    blocks = [block.strip() for block in normalize_text(text).split("\n\n") if block.strip()]
    if not blocks:
        return "<p></p>"
    rendered: list[str] = []
    for block in blocks:
        position = 0
        paragraph_parts: list[str] = []
        for match in IMAGE_MARKER_RE.finditer(block):
            before = block[position : match.start()]
            if before.strip():
                paragraph_parts.append(html.escape(before).replace("\n", "<br/>"))
            if paragraph_parts:
                rendered.append(f"<p>{''.join(paragraph_parts)}</p>")
                paragraph_parts = []
            image_id = match.group(1)
            image = image_by_id.get(image_id)
            if image:
                alt = html.escape(image.get("alt") or image_id)
                filename = html.escape(image["filename"])
                rendered.append(
                    f'<figure><img src="images/{filename}" alt="{alt}"/></figure>'
                )
            position = match.end()
        after = block[position:]
        if after.strip():
            paragraph_parts.append(html.escape(after).replace("\n", "<br/>"))
        if paragraph_parts:
            rendered.append(f"<p>{''.join(paragraph_parts)}</p>")
    return "\n".join(rendered) or "<p></p>"


def _referenced_image_ids(chapters: list[dict]) -> set[str]:
    found: set[str] = set()
    for chapter in chapters:
        found.update(IMAGE_MARKER_RE.findall(chapter.get("content", "")))
    return found
