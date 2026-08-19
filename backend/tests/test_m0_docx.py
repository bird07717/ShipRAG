from __future__ import annotations

from app.m0.docx_probe import inspect_docx
from app.m0.fixtures import create_docx_fixture


def test_mammoth_and_ooxml_preserve_required_docx_structure(tmp_path) -> None:
    path = tmp_path / "m0-sample.docx"
    create_docx_fixture(path)

    report = inspect_docx(path)

    assert report.status == "passed"
    assert report.heading_found
    assert report.paragraph_found
    assert report.table_found
    assert report.mammoth_image_count == 1
    assert report.media_files == ["word/media/image1.png"]
    assert report.ordered_blocks == [
        "TEXT:数据库配置",
        "TEXT:数据库默认端口为3306。",
        "TABLE:参数说明port数据库端口",
        "TEXT:下面截图展示默认端口：",
        "IMAGE:media/image1.png",
        "TEXT:截图后的说明文字。",
    ]
    assert report.warnings == []
