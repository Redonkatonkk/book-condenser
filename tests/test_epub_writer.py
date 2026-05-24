from __future__ import annotations

from pathlib import Path

from ebooklib import ITEM_DOCUMENT, epub

from app.epub_writer import write_condensed_epub


def test_write_condensed_epub_is_readable(tmp_path: Path) -> None:
    output = tmp_path / "out.epub"
    write_condensed_epub(
        output,
        identifier="job-1",
        title="测试书",
        author="作者",
        chapters=[
            {"title": "第一章", "content": "浓缩后的第一章。"},
            {"title": "第二章", "content": "浓缩后的第二章。"},
        ],
    )

    assert output.exists()
    parsed = epub.read_epub(str(output))
    docs = list(parsed.get_items_of_type(ITEM_DOCUMENT))
    assert len(docs) >= 2
    assert parsed.get_metadata("DC", "title")[0][0] == "测试书 - 浓缩版"
