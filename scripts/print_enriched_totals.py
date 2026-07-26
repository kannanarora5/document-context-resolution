#!/usr/bin/env python3
"""Print authoritative totals rough cost inputs from enriched JSON files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GLOB = ROOT / "output" / "enriched"


def summarize(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    totals = data.get("totals") or {}
    resolver = data.get("resolver") or {}
    catalog = data.get("section_catalog") or []
    catalog_size = resolver.get("section_catalog_size") or len(catalog)
    llm_chunks = totals.get("llm_chunks")
    chunk_count = data.get("chunk_count") or len(data.get("chunks") or [])

    # Rough prompt-size estimate: catalog JSON dominates input for this design.
    catalog_chars = len(json.dumps(catalog, ensure_ascii=False)) if catalog else 0
    catalog_tokens_est = catalog_chars // 4 if catalog_chars else None
    # Per-call overhead beyond catalog (system + chunk text + context)
    other_in_tokens = 1500
    out_tokens = 400  # typical compact JSON reply assumption

    print(f"=== {path.name} ===")
    print(f"doc_id:                 {data.get('doc_id')}")
    print(f"chunk_count:            {chunk_count}")
    print(f"llm_chunks:             {llm_chunks}")
    print(f"section_catalog_size:   {catalog_size}")
    print(f"definition_attachments: {totals.get('definition_attachments')}")
    print(f"references_detected:    {totals.get('references_detected')}")
    print(f"references_resolved:    {totals.get('references_resolved')}")
    print(f"references_unresolved:  {totals.get('references_unresolved')}")
    print(f"references_ambiguous:   {totals.get('references_ambiguous')}")
    if catalog_tokens_est is not None and llm_chunks:
        in_tokens = llm_chunks * (catalog_tokens_est + other_in_tokens)
        out_total = llm_chunks * out_tokens
        in_cost = in_tokens / 1_000_000 * 0.15
        out_cost = out_total / 1_000_000 * 0.60
        print(f"catalog_json_chars:     {catalog_chars}")
        print(f"catalog_tokens_est:     ~{catalog_tokens_est}")
        print(f"est_input_tokens:       ~{in_tokens:,}  (catalog+~{other_in_tokens}/call × {llm_chunks})")
        print(f"est_output_tokens:      ~{out_total:,}  (~{out_tokens}/call × {llm_chunks})")
        print(f"est_cost_usd_4o_mini:   ~${in_cost + out_cost:.2f}  (${in_cost:.2f} in + ${out_cost:.2f} out)")
        print(
            "  assumptions: ~4 chars/token; +1500 non-catalog input tokens/call; "
            "~400 output tokens/call; $0.15/$0.60 per 1M in/out."
        )
    print()


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        paths = sorted(DEFAULT_GLOB.glob("*.json"))
    if not paths:
        print(f"No enriched JSON found under {DEFAULT_GLOB}", file=sys.stderr)
        return 1
    for path in paths:
        summarize(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
