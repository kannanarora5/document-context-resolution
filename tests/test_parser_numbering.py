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


def test_paren_units_nest_under_article_not_sibling():
    """Art. 4 defs must keep Article 4 in the breadcrumb (not replace it)."""
    from parser import attach_breadcrumbs, relevel_numbered_headings

    blocks = [
        {"type": "heading", "level": 1, "text": "REGULATIONS", "page": 1},
        {"type": "heading", "level": 2, "text": "(1)", "page": 1},
        {"type": "paragraph", "text": "Recital body.", "page": 1},
        {"type": "heading", "level": 1, "text": "Article 4", "page": 33},
        {"type": "heading", "level": 2, "text": "(1)", "page": 33},
        {"type": "paragraph", "text": "'personal data' means …", "page": 33},
        {"type": "heading", "level": 2, "text": "(2)", "page": 33},
        {"type": "paragraph", "text": "'processing' means …", "page": 33},
        {"type": "heading", "level": 1, "text": "Article 5", "page": 35},
    ]
    releveled = relevel_numbered_headings(blocks)
    crumbs = attach_breadcrumbs(releveled, doc_label="GDPR")

    # Recital under REGULATIONS (level-1 anchor).
    assert crumbs[1]["breadcrumb"] == "GDPR › REGULATIONS › (1)"
    # Art. 4 defs nest under Article 4 (not siblings that replace it).
    assert crumbs[4]["breadcrumb"] == "GDPR › REGULATIONS › Article 4 › (1)"
    assert crumbs[6]["breadcrumb"] == "GDPR › REGULATIONS › Article 4 › (2)"
    assert crumbs[8]["breadcrumb"] == "GDPR › REGULATIONS › Article 5"
    # Article 5 must clear Art. 4 defs from the stack.
    assert "Article 4" not in crumbs[8]["breadcrumb"]
    assert "(2)" not in crumbs[8]["breadcrumb"]


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


def test_rejects_sentence_fragment_headings():
    from parser import _is_plausible_title

    assert not _is_plausible_title(
        "Apple is committed to upholding internationally recognized human rights,"
    )
    assert not _is_plausible_title(
        "part-time, and temporary employees, employed directly or via a third party (“Workers”)."
    )
    assert not _is_plausible_title("with the Standards.")
    assert not _is_plausible_title(
        "Our deep commitment to respecting human rights is stated in our Human Rights Policy."
    )
    assert _is_plausible_title("Anti-Discrimination")
    assert _is_plausible_title("Our Expectations")
    assert _is_plausible_title("United Nations Guiding Principles on Business")


def test_display_title_survives_sidebar_noise():
    """Apple Standard titles share text with 12pt TOC; keep 56pt display lines."""
    noise = {"Anti-Discrimination", "Management Systems"}
    kept = classify_line(
        {"text": "Anti-Discrimination", "size": 56.0, "page": 18, "y0": 200.0},
        body_size=17.0,
        level_map={56.0: 1, 19.0: 2},
        noise_lines=noise,
    )
    assert kept is not None and kept[0]["type"] == "heading"

    dropped = classify_line(
        {"text": "Anti-Discrimination", "size": 12.0, "page": 18, "y0": 40.0},
        body_size=17.0,
        level_map={56.0: 1},
        noise_lines=noise,
    )
    assert dropped is None


def test_absorb_lowercase_title_wrap():
    from parser import absorb_heading_continuations

    blocks = [
        {
            "type": "heading",
            "level": 1,
            "text": "United Nations Guiding Principles on Business",
            "page": 12,
            "y0": 100.0,
            "size": 24.0,
        },
        {
            "type": "paragraph",
            "text": "and Human Rights",
            "page": 12,
            "y0": 118.0,
            "size": 24.0,
        },
    ]
    out = absorb_heading_continuations(blocks)
    assert len(out) == 1
    assert out[0]["type"] == "heading"
    assert out[0]["text"] == "United Nations Guiding Principles on Business and Human Rights"


def test_absorb_allows_larger_gap_for_short_wrap():
    """Apple display titles can leave ~35pt before 'and Human Rights'."""
    from parser import absorb_heading_continuations

    blocks = [
        {
            "type": "heading",
            "level": 1,
            "text": "United Nations Guiding Principles on Business",
            "page": 12,
            "y0": 405.6,
            "size": 24.0,
        },
        {
            "type": "paragraph",
            "text": "and Human Rights",
            "page": 12,
            "y0": 440.6,
            "size": 24.0,
        },
        {
            "type": "paragraph",
            "text": "Under the UNGPs, businesses are expected to meet their responsibility.",
            "page": 12,
            "y0": 498.8,
            "size": 17.0,
        },
    ]
    out = absorb_heading_continuations(blocks)
    assert out[0]["type"] == "heading"
    assert out[0]["text"] == "United Nations Guiding Principles on Business and Human Rights"
    assert out[1]["type"] == "paragraph"
    assert out[1]["text"].startswith("Under the UNGPs")


def test_comma_title_fragment_is_plausible():
    from parser import _is_plausible_title

    assert _is_plausible_title("Responsible Sourcing of Primary,")
    assert not _is_plausible_title(
        "Apple is committed to upholding internationally recognized human rights,"
    )


def test_effective_version_is_layout_noise():
    from parser import is_layout_noise, classify_line

    for text in [
        "Effective November 11, 2025",
        "Version 5",
        "Effective November 11, 2025Version 5",
        "Effective November 11, 2025 Version 5",
    ]:
        assert is_layout_noise(text), text

    glued = classify_line(
        {
            "text": "Effective November 11, 2025Version 5 The following Standards supplement the Code.",
            "size": 17.0,
            "page": 14,
            "y0": 100.0,
        },
        body_size=17.0,
        level_map={},
        noise_lines=set(),
    )
    assert glued is not None
    assert glued[0]["type"] == "paragraph"
    assert glued[0]["text"].startswith("The following Standards")


def test_bullet_sidebar_noise_dropped():
    noise = {"Anti-Discrimination", "Prevention of Forced Labor"}
    dropped = classify_line(
        {"text": "• Anti-Discrimination", "size": 12.0, "page": 18, "y0": 250.0},
        body_size=17.0,
        level_map={56.0: 1},
        noise_lines=noise,
    )
    assert dropped is None


def test_strip_short_preview_before_restart():
    from parser import strip_preview_indexes

    blocks = [
        {"type": "heading", "level": 1, "text": "Third Party Employment Agencies", "page": 25},
        {"type": "heading", "level": 2, "text": "1. TPEA Worker Safeguards", "page": 25},
        {"type": "heading", "level": 2, "text": "2. TPEA Management", "page": 25},
        {"type": "heading", "level": 2, "text": "1. TPEA Worker Safeguards", "page": 26},
        {"type": "paragraph", "text": "Workers shall be provided with accurate details.", "page": 26},
    ]
    out = strip_preview_indexes(blocks)
    headings = [b["text"] for b in out if b["type"] == "heading"]
    assert headings == [
        "Third Party Employment Agencies",
        "1. TPEA Worker Safeguards",
    ]


def test_dedupe_preview_opener_before_intro_then_real():
    from parser import dedupe_preview_section_openers

    blocks = [
        {"type": "heading", "level": 1, "text": "Machine Safety", "page": 74},
        {"type": "heading", "level": 2, "text": "1. Machine Safety Management Program", "page": 74},
        {
            "type": "paragraph",
            "text": "Supplier shall develop and implement a documented program.",
            "page": 75,
        },
        {"type": "heading", "level": 2, "text": "1. Machine Safety Management Program", "page": 75},
        {"type": "paragraph", "text": "Supplier shall follow the procedures in its program.", "page": 75},
    ]
    out = dedupe_preview_section_openers(blocks)
    headings = [b["text"] for b in out if b["type"] == "heading"]
    assert headings == [
        "Machine Safety",
        "1. Machine Safety Management Program",
    ]
    assert out[1]["page"] == 75


def test_version_stamp_not_leading_stripped():
    """RBA version lines must keep 'Version …'; Apple footers still strip."""
    from parser import _strip_leading_doc_stamp, _is_plausible_title

    assert _strip_leading_doc_stamp("Version 8.0.1 (2025)") == "Version 8.0.1 (2025)"
    assert _strip_leading_doc_stamp(
        "Version 1.0 – Released October 2004."
    ) == "Version 1.0 – Released October 2004."
    assert not _is_plausible_title("Version 8.0.1 (2025)")

    stripped = _strip_leading_doc_stamp(
        "Effective November 11, 2025Version 5 The following Standards supplement the Code."
    )
    assert stripped.startswith("The following Standards")


def test_toc_page_number_matches_sidebar_noise():
    from parser import _noise_lookup_key, _is_sidebar_noise

    assert _noise_lookup_key("Management Systems\t15") == "Management Systems"
    assert _noise_lookup_key("Chemical Management  55") == "Chemical Management"
    noise = {"Management Systems", "Anti-Discrimination"}
    assert _is_sidebar_noise("Management Systems\t15", 17.0, 17.0, noise)
    assert not _is_sidebar_noise("Anti-Discrimination", 56.0, 17.0, noise)


def test_drop_redundant_facility_cover_title():
    from parser import drop_redundant_display_titles

    blocks = [
        {
            "type": "heading",
            "level": 1,
            "text": "Energy, Environmental, and New Facility Investments",
            "page": 98,
        },
        {
            "type": "heading",
            "level": 1,
            "text": "Facility Siting, Energy and Environmental Investments",
            "page": 98,
        },
        {"type": "paragraph", "text": "Suppliers providing investment management.", "page": 99},
    ]
    out = drop_redundant_display_titles(
        blocks,
        part_titles={"Facility Siting, Energy and Environmental Investments"},
    )
    headings = [b["text"] for b in out if b["type"] == "heading"]
    assert headings == ["Facility Siting, Energy and Environmental Investments"]


def test_strip_toc_body_keeps_heading_only():
    from parser import strip_toc_body

    blocks = [
        {"type": "heading", "level": 1, "text": "Table of Contents", "page": 2},
        {"type": "paragraph", "text": "Management Systems 15", "page": 2},
        {"type": "paragraph", "text": "Responsible Sourcing of Primary, Recycled, and Renewable Materials", "page": 2},
        {"type": "heading", "level": 1, "text": "Apple Supplier Code of Conduct", "page": 3},
        {"type": "paragraph", "text": "Apple is committed to upholding human rights.", "page": 3},
    ]
    out = strip_toc_body(blocks)
    assert [b["text"] for b in out] == [
        "Table of Contents",
        "Apple Supplier Code of Conduct",
        "Apple is committed to upholding human rights.",
    ]


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
    test_paren_units_nest_under_article_not_sibling()
    test_paren_oj_footnote_not_recital_heading()
    test_strip_preview_index_keeps_real_section()
    test_norm_collapses_tabs()
    test_rejects_sentence_fragment_headings()
    test_display_title_survives_sidebar_noise()
    test_absorb_lowercase_title_wrap()
    test_absorb_allows_larger_gap_for_short_wrap()
    test_comma_title_fragment_is_plausible()
    test_effective_version_is_layout_noise()
    test_bullet_sidebar_noise_dropped()
    test_strip_short_preview_before_restart()
    test_dedupe_preview_opener_before_intro_then_real()
    test_version_stamp_not_leading_stripped()
    test_toc_page_number_matches_sidebar_noise()
    test_drop_redundant_facility_cover_title()
    test_strip_toc_body_keeps_heading_only()
    print("ok")
