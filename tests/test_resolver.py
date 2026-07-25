"""Regression: glossary + section index + deterministic context resolution."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolver import (  # noqa: E402
    build_glossary,
    build_section_index,
    enrich_document,
    resolve_chunk,
)

SAMPLE_CHUNKS = [
    {
        "chunk_id": "demo-0001",
        "doc_id": "demo",
        "breadcrumb": "DEMO › Preamble",
        "heading": "Preamble",
        "level": 1,
        "pages": [1],
        "text": (
            "To adopt the Code and become a participant (“Participant”), a business "
            "shall declare its support. Foreign Contract Worker (FCW) Worker whose "
            "nationality differs from the worksite country."
        ),
        "char_count": 200,
        "part": 1,
        "part_count": 1,
        "context_before": "",
    },
    {
        "chunk_id": "demo-0002",
        "doc_id": "demo",
        "breadcrumb": "DEMO › Article 5",
        "heading": "Article 5",
        "level": 1,
        "pages": [2],
        "text": (
            "Article 5\n\nFurther processing for archiving purposes shall, in accordance "
            "with Article 89(1), not be considered incompatible. Each Participant and "
            "each FCW shall comply. See also the Wages, Benefits, and Contracts Standard."
        ),
        "char_count": 220,
        "part": 1,
        "part_count": 1,
        "context_before": "",
    },
    {
        "chunk_id": "demo-0003",
        "doc_id": "demo",
        "breadcrumb": "DEMO › Article 89",
        "heading": "Article 89",
        "level": 1,
        "pages": [10],
        "text": (
            "Article 89\n\nSafeguards relating to processing for archiving purposes in "
            "the public interest, scientific or historical research purposes or "
            "statistical purposes."
        ),
        "char_count": 180,
        "part": 1,
        "part_count": 1,
        "context_before": "",
    },
    {
        "chunk_id": "demo-0004",
        "doc_id": "demo",
        "breadcrumb": "DEMO › Table of Contents",
        "heading": "Table of Contents",
        "level": 1,
        "pages": [1],
        "text": "Wages, Benefits and Contracts\t41\nForeign Contract Worker Protections\t28\n",
        "char_count": 80,
        "part": 1,
        "part_count": 1,
        "context_before": "",
    },
    {
        "chunk_id": "demo-0005",
        "doc_id": "demo",
        "breadcrumb": "DEMO › Wages, Benefits and Contracts › 1.1 Minimum Pay",
        "heading": "1.1 Minimum Pay",
        "level": 2,
        "pages": [41],
        "text": (
            "1.1 Minimum Pay\n\nAll Workers shall be paid no less than the Minimum Wage "
            "for all Regular Hours."
        ),
        "char_count": 120,
        "part": 1,
        "part_count": 1,
        "context_before": "",
    },
]


def test_section_index_has_articles():
    index = build_section_index(SAMPLE_CHUNKS)
    assert "article 89" in index
    assert index["article 89"]["heading"] == "Article 89"


def test_glossary_captures_participant_and_fcw():
    glossary = build_glossary(SAMPLE_CHUNKS)
    assert "participant" in glossary
    assert "fcw" in glossary
    assert "Foreign Contract Worker" in glossary["fcw"]["definition"] or \
        "nationality" in glossary["fcw"]["definition"]


def test_article_cross_ref_resolves():
    index = build_section_index(SAMPLE_CHUNKS)
    glossary = build_glossary(SAMPLE_CHUNKS)
    enriched = resolve_chunk(
        SAMPLE_CHUNKS[1], section_index=index, glossary=glossary, use_llm=False
    )
    mentions = {r["mention"]: r for r in enriched["resolved_references"]}
    assert "Article 89(1)" in mentions
    assert mentions["Article 89(1)"]["status"] == "resolved"
    assert "Safeguards" in mentions["Article 89(1)"]["excerpt"]
    terms = {d["term"].lower() for d in enriched["attached_definitions"]}
    assert "participant" in terms
    assert "fcw" in terms


def test_named_standard_resolves_via_toc():
    payload = enrich_document(
        {"doc_id": "demo", "chunks": SAMPLE_CHUNKS}, use_llm=False
    )
    art5 = next(c for c in payload["chunks"] if c["chunk_id"] == "demo-0002")
    named = [
        r for r in art5["resolved_references"]
        if "Wages" in r["mention"]
    ]
    assert named
    assert named[0]["status"] in {"resolved", "ambiguous"}
    if named[0]["status"] == "resolved":
        assert named[0]["excerpt"]


def test_enrich_document_totals():
    payload = enrich_document(
        {"doc_id": "demo", "chunks": SAMPLE_CHUNKS}, use_llm=False
    )
    assert payload["totals"]["references_resolved"] >= 1
    assert payload["totals"]["definition_attachments"] >= 1
    assert payload["glossary_size"] >= 2


if __name__ == "__main__":
    test_section_index_has_articles()
    test_glossary_captures_participant_and_fcw()
    test_article_cross_ref_resolves()
    test_named_standard_resolves_via_toc()
    test_enrich_document_totals()
    print("ok")
