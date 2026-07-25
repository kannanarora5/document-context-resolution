"""Regression: numbering / OJ chrome must not create false headings."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parser import (  # noqa: E402
    _numbering_match,
    classify_line,
    is_layout_noise,
)


def test_article_alone_is_heading_pattern():
    assert _numbering_match("Article 51") is not None
    assert _numbering_match("Article 4") is not None
    assert _numbering_match("article 99") is not None  # case-insensitive


def test_mid_sentence_article_refs_are_not_numbering():
    for text in [
        "Article 51;",
        "Article 8(1).",
        "Article 42(1).",
        "Article 93(2).",
        "Article 17(2) and Article 19;",
        "pursuant to Article 51.",
        "Article 263 TFEU.",
    ]:
        assert _numbering_match(text) is None, text


def test_section_and_chapter_same_rule():
    assert _numbering_match("Section 4") is not None
    assert _numbering_match("Chapter 2") is not None
    assert _numbering_match("Section 4 Right to object") is None
    # Roman CHAPTER form used in OJ/GDPR.
    assert _numbering_match("CHAPTER II") is not None
    assert _numbering_match("CHAPTER I General provisions") is not None


def test_dates_are_not_dotted_numbering():
    """OJ running headers start with dates like 4.5.2016 — not 1.1.1 headings."""
    for text in [
        "4.5.2016 Official Journal of the European Union L 119/1",
        "12.1.2001, p. 1).",
        "28.2.2011, p. 13).",
        "31.7.2002, p. 37).",
    ]:
        assert _numbering_match(text) is None, text


def test_real_dotted_subclauses_still_match():
    assert _numbering_match("1.1 Anti-Discrimination") is not None
    assert _numbering_match("6.1.1 Significant Personal Relationships") is not None


def test_oj_layout_noise():
    assert is_layout_noise("4.5.2016 Official Journal of the European Union L 119/1")
    assert is_layout_noise("L 119/2 Official Journal of the European Union 4.5.2016")
    assert is_layout_noise("12.1.2001, p. 1).")
    assert not is_layout_noise("Article 51")


def test_classify_drops_oj_header():
    line = {
        "text": "4.5.2016 Official Journal of the European Union L 119/1",
        "size": 12.0,
        "page": 1,
        "y0": 40.0,
    }
    result = classify_line(
        line,
        body_size=9.6,
        level_map={12.0: 1},
        noise_lines=set(),
    )
    assert result is None


def test_classify_rejects_wrapped_article_citation():
    line = {
        "text": "Article 51;",
        "size": 10.0,
        "page": 34,
        "y0": 100.0,
    }
    result = classify_line(
        line,
        body_size=10.0,
        level_map={},
        noise_lines=set(),
    )
    assert result is not None
    assert len(result) == 1
    assert result[0]["type"] == "paragraph"


def test_classify_accepts_bare_article():
    line = {
        "text": "Article 51",
        "size": 10.0,
        "page": 65,
        "y0": 200.0,
    }
    result = classify_line(
        line,
        body_size=10.0,
        level_map={},
        noise_lines=set(),
    )
    assert result is not None
    assert len(result) == 1
    assert result[0]["type"] == "heading"
    assert result[0]["level"] == 1


def test_rba_letter_section_vs_signature():
    labor = classify_line(
        {"text": "A. LABOR", "size": 16.0, "page": 2, "y0": 80.0},
        body_size=10.8,
        level_map={16.0: 1},
        noise_lines=set(),
    )
    assert labor is not None and labor[0]["type"] == "heading"

    sig = classify_line(
        {"text": "M. SCHULZ", "size": 14.0, "page": 88, "y0": 400.0},
        body_size=9.6,
        level_map={14.0: 1},
        noise_lines=set(),
    )
    assert sig is not None and sig[0]["type"] == "paragraph"


def test_paren_recital_becomes_unit():
    result = classify_line(
        {
            "text": "(51)  Personal data which are, by their nature, particularly sensitive",
            "size": 9.6,
            "page": 10,
            "y0": 120.0,
        },
        body_size=9.6,
        level_map={},
        noise_lines=set(),
    )
    assert result is not None
    assert result[0]["type"] == "heading"
    assert result[0]["text"] == "(51)"
    assert result[1]["type"] == "paragraph"
    assert result[1]["text"].startswith("Personal data")


def test_paren_oj_footnote_not_recital_heading():
    result = classify_line(
        {
            "text": "(1) OJ C 229, 31.7.2012, p. 90.",
            "size": 8.5,
            "page": 1,
            "y0": 700.0,
        },
        body_size=9.6,
        level_map={},
        noise_lines=set(),
    )
    assert result is not None
    assert result[0]["type"] != "heading" or result[0]["text"] != "(1)"
    # Small OJ cite should stay non-unit (paragraph / not recital heading).
    assert not (result[0]["type"] == "heading" and result[0]["text"] == "(1)")


def test_strip_preview_index_keeps_real_section():
    from parser import strip_preview_indexes

    preview = [
        {"type": "heading", "level": 1, "text": f"{n}. Item {n}", "page": 15}
        for n in range(1, 13)
    ]
    real = [
        {"type": "heading", "level": 1, "text": "1. Item 1", "page": 16},
        {"type": "paragraph", "text": "Real body text for the section.", "page": 16},
    ]
    out = strip_preview_indexes(preview + real)
    headings = [b for b in out if b["type"] == "heading"]
    assert len(headings) == 1
    assert headings[0]["page"] == 16
    assert any(b["type"] == "paragraph" for b in out)


def test_norm_collapses_tabs():
    from parser import _norm_text

    assert _norm_text("1.\tLabor and Human Rights") == "1. Labor and Human Rights"


if __name__ == "__main__":
    test_article_alone_is_heading_pattern()
    test_mid_sentence_article_refs_are_not_numbering()
    test_section_and_chapter_same_rule()
    test_dates_are_not_dotted_numbering()
    test_real_dotted_subclauses_still_match()
    test_oj_layout_noise()
    test_classify_drops_oj_header()
    test_classify_rejects_wrapped_article_citation()
    test_classify_accepts_bare_article()
    test_rba_letter_section_vs_signature()
    test_paren_recital_becomes_unit()
    test_paren_oj_footnote_not_recital_heading()
    test_strip_preview_index_keeps_real_section()
    test_norm_collapses_tabs()
    print("ok")
