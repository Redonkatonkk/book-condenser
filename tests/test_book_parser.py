from __future__ import annotations

from pathlib import Path
from dataclasses import asdict

from ebooklib import ITEM_DOCUMENT, ITEM_IMAGE, epub

from app.book_parser import parse_book, split_text_into_chapters
from app.epub_writer import write_condensed_epub


def test_split_text_into_chapters_detects_chinese_headings() -> None:
    text = """
第一章 风起
少年离开村庄，带着旧书和疑问上路。

第二章 入城
他抵达城中，发现线索藏在钟楼里。
"""
    chapters = split_text_into_chapters(text, "测试书")
    assert [chapter.title for chapter in chapters] == ["第一章 风起", "第二章 入城"]
    assert all(chapter.original_count > 0 for chapter in chapters)


def test_parse_txt(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("Chapter 1\nHello world.\n\nChapter 2\nThe end.", encoding="utf-8")
    book = parse_book(path, "sample.txt")
    assert book.integrity.file_type == "TXT"
    assert book.integrity.chapter_count == 2
    assert book.chapters[0].title == "Chapter 1"


def test_parse_epub(tmp_path: Path) -> None:
    path = tmp_path / "sample.epub"
    book = epub.EpubBook()
    book.set_identifier("sample")
    book.set_title("EPUB 样书")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="zh")
    chapter.content = "<h1>第一章</h1><p>这是第一章正文，包含足够的内容用于解析。</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)

    parsed = parse_book(path, "sample.epub")
    assert parsed.title == "EPUB 样书"
    assert parsed.integrity.file_type == "EPUB"
    assert parsed.integrity.chapter_count == 1
    assert "第一章" in parsed.chapters[0].title


def test_parse_epub_uses_spine_reading_order(tmp_path: Path) -> None:
    path = tmp_path / "ordered.epub"
    book = epub.EpubBook()
    book.set_identifier("ordered")
    book.set_title("顺序样书")
    book.set_language("zh")

    ch1 = make_epub_chapter("ch1", "001.xhtml", "第1章")
    ch10 = make_epub_chapter("ch10", "010.xhtml", "第10章")
    ch2 = make_epub_chapter("ch2", "002.xhtml", "第2章")
    for chapter in [ch1, ch10, ch2]:
        book.add_item(chapter)
    book.toc = (ch1, ch2, ch10)
    book.spine = ["nav", ch1, ch2, ch10]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)

    parsed = parse_book(path, "ordered.epub")
    assert [chapter.title for chapter in parsed.chapters] == ["第1章", "第2章", "第10章"]


def test_epub_images_are_preserved_as_markers_and_exported(tmp_path: Path) -> None:
    source = tmp_path / "image.epub"
    book = epub.EpubBook()
    book.set_identifier("image-book")
    book.set_title("图片样书")
    book.set_language("zh")
    image = epub.EpubItem(
        uid="pic",
        file_name="images/pic.png",
        media_type="image/png",
        content=small_png_bytes(),
    )
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="zh")
    chapter.content = (
        "<h1>第一章</h1><p>图片之前的正文内容足够用于解析。</p>"
        '<img src="images/pic.png" alt="插图"/>'
        "<p>图片之后的正文内容也足够用于解析。</p>"
    )
    book.add_item(image)
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(source), book)

    parsed = parse_book(source, "image.epub")
    assert parsed.images
    assert "[[BOOK_CONDENSER_IMAGE:img-1]]" in parsed.chapters[0].text

    output = tmp_path / "out.epub"
    write_condensed_epub(
        output,
        identifier="export",
        title=parsed.title,
        author=parsed.author,
        chapters=[{"title": parsed.chapters[0].title, "content": parsed.chapters[0].text}],
        images=[asdict(image_asset) for image_asset in parsed.images],
    )
    exported = epub.read_epub(str(output))
    exported_images = list(exported.get_items_of_type(ITEM_IMAGE))
    assert exported_images
    docs = [item.get_content().decode("utf-8") for item in exported.get_items_of_type(ITEM_DOCUMENT)]
    assert any("<img" in doc and "images/img-1.png" in doc for doc in docs)


def test_parse_minimal_pdf(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    write_minimal_pdf(path, "Chapter 1 Hello world. Chapter 2 The end.")
    parsed = parse_book(path, "sample.pdf")
    assert parsed.integrity.file_type == "PDF"
    assert parsed.integrity.total_count > 0
    assert parsed.chapters


def write_minimal_pdf(path: Path, text: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_start = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode(
            "ascii"
        )
    )
    path.write_bytes(bytes(output))


def make_epub_chapter(uid: str, file_name: str, title: str):
    chapter = epub.EpubHtml(uid=uid, title=title, file_name=file_name, lang="zh")
    chapter.content = f"<h1>{title}</h1><p>{title} 正文内容足够用于解析。</p>"
    return chapter


def small_png_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000a49444154789c6360000002000100"
        "05fe02fea5579a0000000049454e44ae426082"
    )
