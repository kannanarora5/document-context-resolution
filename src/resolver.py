"""LLM context resolver.
Requires OPENAI_API_KEY and/or ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "output" / "chunks"
ENRICHED_DIR = ROOT / "output" / "enriched"

DEFAULT_EXCERPT_CHARS = 900
DEFAULT_CATALOG_HINT = 120
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-latest"

SYSTEM_PROMPT = (
    "You resolve document context for a compliance extraction pipeline. "
    "You do NOT extract risks, controls, or obligations. "
    "Your only job is to resolve cross-references and tag defined terms "
    "so each chunk is self-contained. Reply with compact JSON only."
)


def load_chunks_file(path: str | Path) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"doc_id": path.stem, "chunk_count": len(data), "chunks": data}
    return data


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def build_section_catalog(chunks: list[dict]) -> list[dict]:
    """Compact outline so the LLM can resolve long-range refs without the full doc."""
    catalog: list[dict] = []
    by_key: dict[tuple[str | None, str], dict] = {}
    for ch in chunks:
        heading = ch.get("heading")
        if not heading:
            continue
        key = (ch.get("breadcrumb"), heading)
        hint = _clip(ch.get("text") or "", DEFAULT_CATALOG_HINT)
        if key in by_key:
            entry = by_key[key]
            if hint and hint not in (entry.get("hint") or ""):
                entry["hint"] = _clip((entry.get("hint") or "") + " " + hint, DEFAULT_CATALOG_HINT * 2)
            continue
        entry = {
            "chunk_id": ch["chunk_id"],
            "heading": heading,
            "breadcrumb": ch.get("breadcrumb"),
            "hint": hint,
        }
        by_key[key] = entry
        catalog.append(entry)
    return catalog


def _chunk_lookup(chunks: list[dict]) -> dict[str, dict]:
    return {c["chunk_id"]: c for c in chunks}


def _norm_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _breadcrumbs_compatible(a: str | None, b: str | None) -> bool:
    """True when breadcrumbs are equal or one is a prefix/suffix of the other."""
    left = _norm_key(a)
    right = _norm_key(b)
    if not left or not right:
        return False
    if left == right:
        return True
    sep = " › "
    return (
        left.endswith(sep + right)
        or right.endswith(sep + left)
        or left.startswith(right + sep)
        or right.startswith(left + sep)
    )


def _resolve_target_chunk(
    *,
    source_chunk_id,
    target_heading: str | None,
    target_breadcrumb: str | None,
    chunks_by_id: dict[str, dict],
    catalog_by_key: dict[tuple[str, str], dict],
) -> dict | None:
    cid = source_chunk_id
    if isinstance(cid, list):
        cid = cid[0] if cid else None
    if cid and cid in chunks_by_id:
        return chunks_by_id[cid]

    heading = _norm_key(_clip(target_heading, 200) if target_heading else None)
    crumb = _norm_key(target_breadcrumb)

    if heading and crumb:
        exact = catalog_by_key.get((crumb, heading))
        if exact:
            return exact
        matches = [
            ch
            for ch in chunks_by_id.values()
            if _norm_key(ch.get("heading")) == heading
            and _breadcrumbs_compatible(ch.get("breadcrumb"), target_breadcrumb)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Prefer the longest matching breadcrumb (most specific).
            matches.sort(key=lambda ch: len(_norm_key(ch.get("breadcrumb"))), reverse=True)
            return matches[0]
        return None

    if heading:
        matches = [
            ch for ch in chunks_by_id.values() if _norm_key(ch.get("heading")) == heading
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    return None


def _fill_excerpts(
    refs: list[dict],
    defs: list[dict],
    *,
    chunks_by_id: dict[str, dict],
    catalog_by_key: dict[tuple[str, str], dict],
) -> None:
    """Attach real section text after the LLM chooses targets."""
    for r in refs:
        if r.get("status") != "resolved":
            continue
        if r.get("excerpt"):
            r["excerpt"] = _clip(str(r["excerpt"]), DEFAULT_EXCERPT_CHARS)
            continue
        cid = r.get("source_chunk_id")
        if isinstance(cid, list):
            cid = cid[0] if cid else None
        target = _resolve_target_chunk(
            source_chunk_id=cid,
            target_heading=r.get("target_heading"),
            target_breadcrumb=r.get("target_breadcrumb"),
            chunks_by_id=chunks_by_id,
            catalog_by_key=catalog_by_key,
        )
        if target:
            r["source_chunk_id"] = target.get("chunk_id") or cid
            if not r.get("target_heading"):
                r["target_heading"] = target.get("heading")
            if not r.get("target_breadcrumb"):
                r["target_breadcrumb"] = target.get("breadcrumb")
            r["excerpt"] = _clip(target.get("text") or "", DEFAULT_EXCERPT_CHARS)
        elif r.get("target_heading") and not cid:
            # Keep LLM status, but do not attach a wrong same-titled section.
            note = r.get("note") or ""
            warn = "ambiguous heading without unique breadcrumb/chunk_id"
            r["note"] = f"{note}; {warn}".strip("; ") if note else warn

    for d in defs:
        if d.get("definition"):
            continue
        cid = d.get("source_chunk_id")
        target = chunks_by_id.get(cid) if cid else None
        if target is None and d.get("target_heading"):
            target = _resolve_target_chunk(
                source_chunk_id=None,
                target_heading=d.get("target_heading"),
                target_breadcrumb=d.get("target_breadcrumb") or d.get("breadcrumb"),
                chunks_by_id=chunks_by_id,
                catalog_by_key=catalog_by_key,
            )
        if target:
            if not d.get("source_chunk_id"):
                d["source_chunk_id"] = target.get("chunk_id")
            d["definition"] = _clip(target.get("text") or "", 500)


def _ssl_context():
    try:
        import certifi
        import ssl

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _http_json(url: str, headers: dict, body: dict, *, retries: int = 5) -> dict | None:
    data = json.dumps(body).encode("utf-8")
    ctx = _ssl_context()
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries - 1:
                wait = min(60.0, (2 ** attempt) + 1)
                ra = exc.headers.get("Retry-After") if exc.headers else None
                if ra:
                    try:
                        wait = max(wait, float(ra))
                    except ValueError:
                        pass
                print(
                    f"LLM HTTP {exc.code}; retry in {wait:.0f}s "
                    f"({attempt + 1}/{retries})…",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            print(f"LLM HTTPError {exc.code}: {err_body}", file=sys.stderr)
            break
        except Exception as exc:
            last_exc = exc
            break

    if last_exc is not None:
        print(f"LLM call failed ({type(last_exc).__name__}): {last_exc}", file=sys.stderr)
    return None


def call_openai_json(prompt: str, *, model: str) -> dict | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    payload = _http_json(
        "https://api.openai.com/v1/chat/completions",
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
    )
    if not payload:
        return None
    try:
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"OpenAI parse error: {exc}", file=sys.stderr)
        return None


def call_anthropic_json(prompt: str, *, model: str) -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    payload = _http_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        {
            "model": model,
            "max_tokens": 2048,
            "temperature": 0,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    if not payload:
        return None
    try:
        parts = payload.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        # Strip optional markdown fences.
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        print(f"Anthropic parse error: {exc}", file=sys.stderr)
        return None


def call_llm_json(
    prompt: str,
    *,
    provider: str,
    openai_model: str,
    anthropic_model: str,
) -> dict | None:
    if provider == "openai":
        return call_openai_json(prompt, model=openai_model)
    if provider == "anthropic":
        return call_anthropic_json(prompt, model=anthropic_model)
    if provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return call_anthropic_json(prompt, model=anthropic_model)
        if os.environ.get("OPENAI_API_KEY"):
            return call_openai_json(prompt, model=openai_model)
        return None
    raise ValueError(f"Unknown provider: {provider}")


def detect_provider(preferred: str) -> str:
    if preferred != "auto":
        return preferred
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "none"


def build_resolve_prompt(
    chunk: dict,
    *,
    catalog: list[dict],
    catalog_json: str,
) -> str:
    return (
        "Resolve cross-references and tag defined terms for this chunk.\n\n"
        "Rules:\n"
        "- Use SECTION_CATALOG to resolve internal references "
        "(e.g. Article 89, named Standards, Section D).\n"
        "- For each resolved reference set source_chunk_id to the matching "
        "catalog chunk_id (preferred) plus target_heading/breadcrumb. "
        "You may omit excerpt — it will be filled from the source chunk.\n"
        "- Tag defined terms/acronyms that appear in the chunk but are defined "
        "elsewhere (Participant, FCW, controller, etc.). Prefer source_chunk_id; "
        "include a short definition when visible in CONTEXT_BEFORE or catalog hints.\n"
        "- If a reference is outside this document, mark status unresolved.\n"
        "- Do not invent section ids or headings that are not in the catalog.\n"
        "- Do not extract risks/controls/obligations.\n\n"
        "Return JSON with this shape:\n"
        "{\n"
        '  "resolved_references": [\n'
        "    {\n"
        '      "mention": "...",\n'
        '      "status": "resolved"|"unresolved"|"ambiguous",\n'
        '      "target_heading": "..."|null,\n'
        '      "target_breadcrumb": "..."|null,\n'
        '      "source_chunk_id": "..."|null,\n'
        '      "excerpt": "..."|null,\n'
        '      "note": "..."|null\n'
        "    }\n"
        "  ],\n"
        '  "attached_definitions": [\n'
        "    {\n"
        '      "term": "...",\n'
        '      "definition": "...",\n'
        '      "source_chunk_id": "..."|null\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"CHUNK_ID: {chunk.get('chunk_id')}\n"
        f"BREADCRUMB: {chunk.get('breadcrumb')}\n"
        f"HEADING: {chunk.get('heading')}\n"
        f"PAGES: {chunk.get('pages')}\n\n"
        f"CONTEXT_BEFORE:\n{chunk.get('context_before') or '(none)'}\n\n"
        f"CHUNK_TEXT:\n{chunk.get('text') or ''}\n\n"
        f"SECTION_CATALOG ({len(catalog)} entries):\n{catalog_json}\n"
    )


def _normalize_llm_result(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {"resolved_references": [], "attached_definitions": [], "llm_ok": False}

    refs = result.get("resolved_references") or result.get("references") or []
    defs = result.get("attached_definitions") or result.get("definitions") or []
    if not isinstance(refs, list):
        refs = []
    if not isinstance(defs, list):
        defs = []

    clean_refs = []
    for r in refs:
        if not isinstance(r, dict):
            continue
        status = (r.get("status") or "unresolved").lower()
        if status not in {"resolved", "unresolved", "ambiguous"}:
            status = "unresolved"
        clean_refs.append({
            "mention": r.get("mention") or "",
            "status": status,
            "target_heading": r.get("target_heading"),
            "target_breadcrumb": r.get("target_breadcrumb"),
            "source_chunk_id": r.get("source_chunk_id") or r.get("source_chunk_ids"),
            "excerpt": r.get("excerpt"),
            "note": r.get("note"),
            "method": "llm",
        })

    clean_defs = []
    for d in defs:
        if not isinstance(d, dict):
            continue
        term = (d.get("term") or "").strip()
        definition = (d.get("definition") or "").strip()
        if not term or not definition:
            continue
        clean_defs.append({
            "term": term,
            "definition": _clip(definition, 500),
            "source_chunk_id": d.get("source_chunk_id"),
            "method": "llm",
        })

    return {
        "resolved_references": clean_refs,
        "attached_definitions": clean_defs,
        "llm_ok": True,
    }


def resolve_chunk_with_llm(
    chunk: dict,
    *,
    catalog: list[dict],
    catalog_json: str,
    chunks_by_id: dict[str, dict],
    provider: str,
    openai_model: str,
    anthropic_model: str,
) -> dict:
    prompt = build_resolve_prompt(chunk, catalog=catalog, catalog_json=catalog_json)
    raw = call_llm_json(
        prompt,
        provider=provider,
        openai_model=openai_model,
        anthropic_model=anthropic_model,
    )
    parsed = _normalize_llm_result(raw)
    catalog_by_key: dict[tuple[str, str], dict] = {}
    for e in catalog:
        key = (_norm_key(e.get("breadcrumb")), _norm_key(e.get("heading")))
        if not key[1]:
            continue
        catalog_by_key[key] = chunks_by_id.get(e["chunk_id"], e)
    _fill_excerpts(
        parsed["resolved_references"],
        parsed["attached_definitions"],
        chunks_by_id=chunks_by_id,
        catalog_by_key=catalog_by_key,
    )

    out = dict(chunk)
    out["attached_definitions"] = parsed["attached_definitions"]
    out["resolved_references"] = parsed["resolved_references"]
    refs = parsed["resolved_references"]
    out["enrichment"] = {
        "definition_count": len(parsed["attached_definitions"]),
        "reference_count": len(refs),
        "resolved_count": sum(1 for r in refs if r["status"] == "resolved"),
        "unresolved_count": sum(1 for r in refs if r["status"] == "unresolved"),
        "ambiguous_count": sum(1 for r in refs if r["status"] == "ambiguous"),
        "method": "llm" if parsed["llm_ok"] else "llm_failed",
    }
    return out


def enrich_document(
    chunks_payload: dict,
    *,
    provider: str = "auto",
    openai_model: str = DEFAULT_OPENAI_MODEL,
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL,
    limit: int | None = None,
    skip_empty: bool = True,
) -> dict:
    chunks = list(chunks_payload["chunks"])
    doc_id = chunks_payload.get("doc_id") or (chunks[0]["doc_id"] if chunks else None)
    catalog = build_section_catalog(chunks)
    catalog_json = json.dumps(catalog, ensure_ascii=False)
    chunks_by_id = _chunk_lookup(chunks)

    resolved_provider = detect_provider(provider)
    if resolved_provider == "none":
        raise RuntimeError(
            "No LLM API key found. Set ANTHROPIC_API_KEY (preferred per assignment) "
            "or OPENAI_API_KEY, then re-run."
        )

    enriched: list[dict] = []
    work = chunks[:limit] if limit else chunks
    for i, ch in enumerate(work, start=1):
        text = (ch.get("text") or "").strip()
        # Heading-only / tiny noise: still keep structure, skip LLM spend.
        if skip_empty and len(text) < 40 and not re.search(
            r"\b(Article|Section|Standard|see |as defined)\b", text, re.I
        ):
            out = dict(ch)
            out["attached_definitions"] = []
            out["resolved_references"] = []
            out["enrichment"] = {
                "definition_count": 0,
                "reference_count": 0,
                "resolved_count": 0,
                "unresolved_count": 0,
                "ambiguous_count": 0,
                "method": "skipped_short",
            }
            enriched.append(out)
            continue

        print(
            f"  [{i}/{len(work)}] LLM resolve {ch.get('chunk_id')} "
            f"({resolved_provider})…",
            file=sys.stderr,
        )
        enriched.append(
            resolve_chunk_with_llm(
                ch,
                catalog=catalog,
                catalog_json=catalog_json,
                chunks_by_id=chunks_by_id,
                provider=resolved_provider,
                openai_model=openai_model,
                anthropic_model=anthropic_model,
            )
        )

    # If --limit was used, append remaining chunks untouched so file stays complete
    if limit and limit < len(chunks):
        for ch in chunks[limit:]:
            out = dict(ch)
            out["attached_definitions"] = []
            out["resolved_references"] = []
            out["enrichment"] = {
                "definition_count": 0,
                "reference_count": 0,
                "resolved_count": 0,
                "unresolved_count": 0,
                "ambiguous_count": 0,
                "method": "not_processed",
            }
            enriched.append(out)

    totals = {
        "definition_attachments": sum(
            c["enrichment"]["definition_count"] for c in enriched
        ),
        "references_detected": sum(
            c["enrichment"]["reference_count"] for c in enriched
        ),
        "references_resolved": sum(
            c["enrichment"]["resolved_count"] for c in enriched
        ),
        "references_unresolved": sum(
            c["enrichment"]["unresolved_count"] for c in enriched
        ),
        "references_ambiguous": sum(
            c["enrichment"]["ambiguous_count"] for c in enriched
        ),
        "llm_chunks": sum(
            1 for c in enriched if c["enrichment"].get("method") == "llm"
        ),
    }

    return {
        "doc_id": doc_id,
        "chunk_count": len(enriched),
        "resolver": {
            "method": "llm",
            "provider": resolved_provider,
            "openai_model": openai_model,
            "anthropic_model": anthropic_model,
            "section_catalog_size": len(catalog),
        },
        "totals": totals,
        "section_catalog": catalog,
        "chunks": enriched,
    }


def write_enriched_json(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out_path


def enrich_chunks_file(
    chunks_path: str | Path,
    *,
    provider: str = "auto",
    openai_model: str = DEFAULT_OPENAI_MODEL,
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL,
    limit: int | None = None,
    out_path: str | Path | None = None,
) -> dict:
    chunks_path = Path(chunks_path)
    payload = load_chunks_file(chunks_path)
    enriched = enrich_document(
        payload,
        provider=provider,
        openai_model=openai_model,
        anthropic_model=anthropic_model,
        limit=limit,
    )
    dest = Path(out_path) if out_path else ENRICHED_DIR / f"{chunks_path.stem}.json"
    write_enriched_json(enriched, dest)
    return enriched


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=(
            "LLM context resolution: send each chunk (breadcrumb + context) "
            "to an LLM to resolve cross-refs and tag definitions → enriched JSON."
        )
    )
    ap.add_argument(
        "chunks",
        nargs="?",
        help="Path to one chunks JSON file. Omit to process output/chunks/*.json.",
    )
    ap.add_argument(
        "--provider",
        choices=["auto", "anthropic", "openai"],
        default="auto",
        help="LLM provider (default auto: Anthropic if key set, else OpenAI).",
    )
    ap.add_argument("--openai-model", default=DEFAULT_OPENAI_MODEL)
    ap.add_argument("--anthropic-model", default=DEFAULT_ANTHROPIC_MODEL)
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only LLM-resolve the first N chunks (smoke test / cost control).",
    )
    args = ap.parse_args()

    targets = (
        [Path(args.chunks)] if args.chunks else sorted(CHUNKS_DIR.glob("*.json"))
    )
    if not targets:
        print(f"No chunk JSON found in {CHUNKS_DIR}")
        sys.exit(1)

    for path in targets:
        enriched = enrich_chunks_file(
            path,
            provider=args.provider,
            openai_model=args.openai_model,
            anthropic_model=args.anthropic_model,
            limit=args.limit,
        )
        t = enriched["totals"]
        print(
            f"{path.name}: chunks={enriched['chunk_count']} "
            f"provider={enriched['resolver']['provider']} "
            f"llm_chunks={t['llm_chunks']} "
            f"defs_attached={t['definition_attachments']} "
            f"refs={t['references_detected']} "
            f"resolved={t['references_resolved']} "
            f"unresolved={t['references_unresolved']} "
            f"ambiguous={t['references_ambiguous']} "
            f"-> {ENRICHED_DIR / path.name}"
        )
