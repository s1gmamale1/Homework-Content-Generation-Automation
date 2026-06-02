from app.services.notion import blocks


def test_heading_levels():
    b = blocks.make_heading("Title", level=2)
    assert b["type"] == "heading_2"
    assert b["heading_2"]["rich_text"][0]["text"]["content"] == "Title"


def test_paragraph_chunks_over_2000_chars():
    long = "x" * 4500
    b = blocks.make_paragraph(long)
    segs = b["paragraph"]["rich_text"]
    assert len(segs) == 3  # 2000 + 2000 + 500
    assert all(len(s["text"]["content"]) <= 2000 for s in segs)


def test_parse_rich_text_bold_and_plain():
    segs = blocks.parse_rich_text("plain **bold** end")
    contents = [s["text"]["content"] for s in segs]
    assert "bold" in contents
    bold_seg = next(s for s in segs if s["text"]["content"] == "bold")
    assert bold_seg["annotations"]["bold"] is True


def test_parse_rich_text_preserves_lone_asterisk_multiplication():
    segs = blocks.parse_rich_text("5 * 3 = 15")
    joined = "".join(s["text"]["content"] for s in segs)
    assert joined == "5 * 3 = 15"


def test_markdown_to_blocks_headings_bullets_divider():
    md = "# Heading\n\n- item one\n- item two\n\n---\n\nA paragraph."
    out = blocks.markdown_to_notion_blocks(md)
    types = [b["type"] for b in out]
    assert types[0] == "heading_1"
    assert "bulleted_list_item" in types
    assert "divider" in types
    assert types[-1] == "paragraph"


def test_file_upload_block_shape():
    b = blocks.make_file_upload_block("upl_123", "homework.md")
    assert b["type"] == "file"
    assert b["file"]["type"] == "file_upload"
    assert b["file"]["file_upload"]["id"] == "upl_123"
    assert b["file"]["name"] == "homework.md"
