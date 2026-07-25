"""Regression: section chunker splits on headings and keeps breadcrumbs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chunker import chunk_blocks, parse_markdown_blocks  # noqa: E402

SAMPLE_MD = """# RBA — structured extract

<!-- breadcrumb: RBA › A. LABOR | page: 2 -->
## A. LABOR

<!-- breadcrumb: RBA › A. LABOR | page: 2 -->
Participants commit to respect the human rights of workers.

<!-- breadcrumb: RBA › A. LABOR › 1) Prohibition of Forced Labor | page: 2 -->
### 1) Prohibition of Forced Labor

<!-- breadcrumb: RBA › A. LABOR › 1) Prohibition of Forced Labor | page: 2 -->
Forced labor in any form is not permitted.

<!-- breadcrumb: RBA › A. LABOR › 2) Young Workers | page: 2 -->
### 2) Young Workers

<!-- breadcrumb: RBA › A. LABOR › 2) Young Workers | page: 2 -->
Child labor shall not be used.
"""


def test_parse_blocks_reads_breadcrumb_and_types():
    blocks = parse_markdown_blocks(SAMPLE_MD)
    types = [b["type"] for b in blocks]
    assert types == ["heading", "paragraph", "heading", "paragraph", "heading", "paragraph"]
    assert blocks[0]["breadcrumb"] == "RBA › A. LABOR"
    assert blocks[2]["text"] == "1) Prohibition of Forced Labor"


def test_chunk_splits_on_each_heading():
    blocks = parse_markdown_blocks(SAMPLE_MD)
    chunks = chunk_blocks(blocks, doc_id="rba_code_of_conduct", context_chars=50)
    assert len(chunks) == 3
    assert chunks[0]["heading"] == "A. LABOR"
    assert "Participants commit" in chunks[0]["text"]
    assert chunks[1]["breadcrumb"].endswith("1) Prohibition of Forced Labor")
    assert "Forced labor" in chunks[1]["text"]
    assert chunks[2]["heading"] == "2) Young Workers"


def test_context_before_comes_from_previous_chunk():
    blocks = parse_markdown_blocks(SAMPLE_MD)
    chunks = chunk_blocks(blocks, doc_id="rba", context_chars=40)
    assert chunks[0]["context_before"] == ""
    assert chunks[1]["context_before"]
    assert chunks[1]["context_before"] in chunks[0]["text"]


def test_chunk_ids_are_stable():
    blocks = parse_markdown_blocks(SAMPLE_MD)
    chunks = chunk_blocks(blocks, doc_id="rba", context_chars=0)
    assert [c["chunk_id"] for c in chunks] == ["rba-0001", "rba-0002", "rba-0003"]


def test_oversized_section_splits_on_paragraphs():
    paras = [f"Paragraph {i}: " + ("word " * 40) for i in range(12)]
    body = "\n\n".join(paras)
    blocks = [
        {
            "type": "heading",
            "level": 1,
            "text": "REGULATIONS",
            "breadcrumb": "GDPR › REGULATIONS",
            "page": 1,
        },
        {
            "type": "paragraph",
            "text": body,
            "breadcrumb": "GDPR › REGULATIONS",
            "page": 1,
        },
    ]
    chunks = chunk_blocks(blocks, doc_id="gdpr", context_chars=0, max_chars=800)
    assert len(chunks) > 1
    assert all(c["char_count"] <= 800 for c in chunks)
    assert all(c["heading"] == "REGULATIONS" for c in chunks)
    assert chunks[0]["part"] == 1
    assert chunks[0]["part_count"] == len(chunks)
    assert chunks[-1]["part"] == len(chunks)
    # Reassembled body (minus heading line on first piece) still covers content.
    joined = "\n\n".join(c["text"] for c in chunks)
    assert "Paragraph 0:" in joined
    assert "Paragraph 11:" in joined


if __name__ == "__main__":
    test_parse_blocks_reads_breadcrumb_and_types()
    test_chunk_splits_on_each_heading()
    test_context_before_comes_from_previous_chunk()
    test_chunk_ids_are_stable()
    test_oversized_section_splits_on_paragraphs()
    print("ok")
