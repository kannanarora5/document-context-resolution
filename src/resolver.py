"""Context resolver: attach defs + cross-refs onto section chunks.

Deterministic lookup first (glossary registry + section index). Optional LLM
pass only for long excerpts / ambiguous named-section matches when OPENAI_API_KEY
is set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "output" / "chunks"
ENRICHED_DIR = ROOT / "output" / "enriched"

DEFAULT_EXCERPT_CHARS = 900
DEFAULT_DEF_CHARS = 500

ARTICLE_HEADING_RE = re.compile(r"^Article\s+(\d+)\b", re.I)
ARTICLE_MENTION_RE = re.compile(
    r"\bArticle\s+(\d+)(?:\s*\(([0-9a-z]+)\))?",
    re.I,
)
# Prefer "the <Named Title> Standard" (Apple-style named section refs).
NAMED_STANDARD_RE = re.compile(
    r"\bthe\s+([A-Z][A-Za-z0-9][A-Za-z0-9 ,/&\-]{3,70}?)\s+Standard\b"
)
# Term (ACRONYM) — capture acronym defs
ACRONYM_DEF_RE = re.compile(
    r"([A-Z][A-Za-z0-9][A-Za-z0-9/ &\-]{1,80}?)\s*\(([A-Z]{2,10}s?)\)"
)
# ACRONYM or Full Name <definition...>
ACRONYM_OR_RE = re.compile(
    r"\b([A-Z]{2,10})\s+or\s+([A-Z][A-Za-z0-9][A-Za-z0-9/ &\-]{1,80}?)\s+([A-Z][^.]{10,220}\.)"
)
# GDPR-style multi-def block: (1) 'term' means ... (2) 'other' means ...
GDPR_DEF_RE = re.compile(
    r"\((\d+)\)\s*[‘'']([^‘'']+)[’'']\s*means\s+(.+?)(?=\(\d+\)\s*[‘'']|\Z)",
    re.S,
)
# Split Art. 4 unit chunk: heading "(N)", body "'term' means …"
GDPR_SINGLE_DEF_RE = re.compile(
    r"[‘'']([^‘'']+)[’'']\s*means\s+(.+)",
    re.S,
)
PAREN_HEADING_RE = re.compile(r"^\((\d+)\)")
ARTICLE_IN_CRUMB_RE = re.compile(r"Article\s+(\d+)\b", re.I)
# RBA-style: become a participant (“Participant”)
QUOTED_ALIAS_RE = re.compile(
    r"\b((?:[A-Za-z]+[ \-]){0,5}[A-Za-z]+)\s*\([\"“]([A-Z][A-Za-z0-9\-]{2,40})[\"”]\)"
)

SKIP_STANDARD_NAMES = {
    "this",
    "the",
    "a",
    "an",
    "applicable",
    "relevant",
    "code and this",
    "code and",
}

STOP_ACRONYMS = {
    "A", "AN", "THE", "AND", "OR", "OF", "TO", "IN", "ON", "FOR", "AS", "BY",
    "EU", "US", "UK", "ID", "PDF", "CEO", "HR", "IT", "AI", "OK",
}


def _norm_key(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\b(the|a|an|standard|standards)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_set(text: str) -> set[str]:
    return {t for t in _norm_key(text).split() if len(t) > 2}


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def load_chunks_file(path: str | Path) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"doc_id": path.stem, "chunk_count": len(data), "chunks": data}
    return data


def build_section_index(chunks: list[dict]) -> dict[str, dict]:
    """Map normalized section keys → heading metadata + merged excerpt text."""
    index: dict[str, dict] = {}

    # Group multi-part chunks that share the same heading.
    by_heading: dict[str, list[dict]] = {}
    for ch in chunks:
        heading = ch.get("heading")
        if not heading:
            continue
        by_heading.setdefault(heading, []).append(ch)

    for heading, group in by_heading.items():
        group = sorted(group, key=lambda c: (c.get("part") or 1, c["chunk_id"]))
        merged = "\n\n".join(c["text"] for c in group)
        entry = {
            "heading": heading,
            "breadcrumb": group[0].get("breadcrumb"),
            "source_chunk_ids": [c["chunk_id"] for c in group],
            "text": merged,
        }
        keys = {_norm_key(heading), heading.lower()}
        am = ARTICLE_HEADING_RE.match(heading.strip())
        if am:
            keys.add(f"article {am.group(1)}")
            keys.add(f"article_{am.group(1)}")
        pm = PAREN_HEADING_RE.match(heading.strip())
        crumb = group[0].get("breadcrumb") or ""
        art_in_crumb = ARTICLE_IN_CRUMB_RE.search(crumb)
        if pm:
            num = pm.group(1)
            keys.add(f"({num})")
            if art_in_crumb:
                # Art. 4 › (1) is a definition unit, not a recital.
                art_n = art_in_crumb.group(1)
                keys.add(f"article {art_n}({num})")
                keys.add(f"article {art_n} ({num})")
                keys.add(f"article_{art_n}_{num}")
            else:
                keys.add(f"recital {num}")
                keys.add(f"recital_{num}")
        for key in keys:
            if key and key not in index:
                index[key] = entry

        # Also index leaf title after numbering: "1) Prohibition of Forced Labor"
        leaf = re.sub(r"^[\d\.\)\t\s]+", "", heading).strip()
        if leaf and leaf != heading:
            nk = _norm_key(leaf)
            if nk and nk not in index:
                index[nk] = entry

    # TOC-style named sections (Apple): harvest "Title<tab>page" entries.
    toc_re = re.compile(r"([A-Z][A-Za-z0-9][A-Za-z0-9 ,/&\-]{3,70}?)\t+\d+")
    for ch in chunks:
        for title in toc_re.findall(ch.get("text") or ""):
            title = title.strip()
            nk = _norm_key(title)
            if not nk or nk in index:
                continue
            target = _best_chunk_for_name(chunks, title, after_id=ch["chunk_id"])
            if target is None:
                # Keep a TOC stub so named refs still have a candidate key.
                index[nk] = {
                    "heading": title,
                    "breadcrumb": ch.get("breadcrumb"),
                    "source_chunk_ids": [ch["chunk_id"]],
                    "text": title,
                    "toc_title": title,
                }
                continue
            index[nk] = {
                "heading": target.get("heading") or title,
                "breadcrumb": target.get("breadcrumb"),
                "source_chunk_ids": [target["chunk_id"]],
                "text": target["text"],
                "toc_title": title,
            }

    return index


def _best_chunk_for_name(
    chunks: list[dict], name: str, *, after_id: str | None = None
) -> dict | None:
    want = _token_set(name)
    if not want:
        return None
    started = after_id is None
    best = None
    best_score = 0.0
    for ch in chunks:
        if not started:
            if ch["chunk_id"] == after_id:
                started = True
            continue
        body_len = ch.get("char_count") or len(ch.get("text") or "")
        if body_len < 80:
            continue
        blob = f"{ch.get('heading') or ''} {ch.get('breadcrumb') or ''}"
        have = _token_set(blob)
        if not have:
            continue
        overlap = len(want & have) / len(want)
        # Prefer substantial content under overlapping breadcrumbs.
        if overlap > best_score or (
            overlap == best_score and best is not None and body_len > (
                best.get("char_count") or 0
            )
        ):
            best_score = overlap
            best = ch
    return best if best_score >= 0.34 else None


def build_glossary(chunks: list[dict]) -> dict[str, dict]:
    """Collect defined terms/acronyms → first definition seen."""
    glossary: dict[str, dict] = {}

    def add(term: str, definition: str, chunk_id: str, kind: str) -> None:
        term = term.strip()
        definition = _clip(definition.strip(), DEFAULT_DEF_CHARS)
        if not term or not definition:
            return
        if kind == "quoted_alias_phrase":
            return
        if kind == "quoted_alias" and len(term.split()) > 1:
            # Keep the alias token (Participant), drop long left phrases.
            return
        key = term.lower()
        if key in glossary:
            return
        if term.upper() in STOP_ACRONYMS and len(term) <= 3:
            return
        glossary[key] = {
            "term": term,
            "definition": definition,
            "source_chunk_id": chunk_id,
            "kind": kind,
        }

    for ch in chunks:
        text = ch.get("text") or ""
        cid = ch["chunk_id"]
        heading = (ch.get("heading") or "").strip()
        crumb = ch.get("breadcrumb") or ""

        for m in GDPR_DEF_RE.finditer(text):
            add(m.group(2), f"means {m.group(3).strip()}", cid, "gdpr_article_def")

        # Per-unit Art. 4 chunks: body starts with 'term' means … (no leading (N)).
        if PAREN_HEADING_RE.match(heading) and ARTICLE_IN_CRUMB_RE.search(crumb):
            body = text
            if body.startswith(heading):
                body = body[len(heading):].lstrip()
            sm = GDPR_SINGLE_DEF_RE.search(body)
            if sm:
                add(sm.group(1), f"means {sm.group(2).strip()}", cid, "gdpr_article_def")

        for m in QUOTED_ALIAS_RE.finditer(text):
            alias = m.group(2).strip()
            start = max(0, m.start() - 120)
            end = min(len(text), m.end() + 180)
            add(alias, text[start:end], cid, "quoted_alias")

        for m in ACRONYM_OR_RE.finditer(text):
            add(m.group(1), f"{m.group(2)} — {m.group(3)}", cid, "acronym_or")

        for m in ACRONYM_DEF_RE.finditer(text):
            full, acr = m.group(1).strip(), m.group(2).strip()
            if acr.upper() in STOP_ACRONYMS:
                continue
            if len(full.split()) > 12:
                continue
            after = text[m.end(): m.end() + 240].strip()
            # Stop before the next glossary letter-entry (Apple dump: "G Good Faith").
            stop = re.search(r"\s+[A-Z]\s+[A-Z][a-z]", after)
            if stop:
                after = after[: stop.start()].strip()
            after = re.split(r"(?<=\.)\s+(?=[A-Z])", after, maxsplit=1)[0].strip()
            if after and after[0].isupper() and not after.startswith("("):
                definition = f"{full}. {after}"
            else:
                definition = full
            add(acr, definition, cid, "acronym_paren")
            add(full, f"({acr})", cid, "acronym_expansion")

    return glossary


def lookup_section(section_index: dict[str, dict], mention: str) -> dict | None:
    keys = [
        _norm_key(mention),
        mention.lower().strip(),
    ]
    am = ARTICLE_MENTION_RE.fullmatch(mention.strip()) or ARTICLE_HEADING_RE.match(
        mention.strip()
    )
    if am:
        num = am.group(1)
        # Prefer paragraph-specific Art. 4(1) units when indexed.
        para = am.group(2) if am.lastindex and am.lastindex >= 2 else None
        if para:
            keys.extend([
                f"article {num}({para})",
                f"article {num} ({para})",
                f"article_{num}_{para}",
            ])
        keys.extend([f"article {num}", f"article_{num}"])

    for key in keys:
        if key in section_index:
            return section_index[key]

    # Fuzzy token overlap for named sections.
    want = _token_set(mention)
    if len(want) < 2:
        return None
    best_key = None
    best_score = 0.0
    for key, entry in section_index.items():
        have = _token_set(entry.get("toc_title") or entry.get("heading") or key)
        if not have:
            continue
        score = len(want & have) / len(want)
        if score > best_score:
            best_score = score
            best_key = key
    if best_key and best_score >= 0.6:
        return section_index[best_key]
    return None


def find_ambiguous_candidates(
    section_index: dict[str, dict], mention: str, *, limit: int = 5
) -> list[dict]:
    want = _token_set(mention)
    scored: list[tuple[float, dict]] = []
    seen = set()
    for entry in section_index.values():
        hid = entry["source_chunk_ids"][0]
        if hid in seen:
            continue
        have = _token_set(entry.get("toc_title") or entry.get("heading") or "")
        if not have or not want:
            continue
        score = len(want & have) / len(want)
        if score >= 0.3:
            seen.add(hid)
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "heading": e["heading"],
            "breadcrumb": e.get("breadcrumb"),
            "score": round(s, 3),
        }
        for s, e in scored[:limit]
    ]


def _self_article_num(chunk: dict) -> str | None:
    h = chunk.get("heading") or ""
    m = ARTICLE_HEADING_RE.match(h.strip())
    return m.group(1) if m else None


def detect_article_mentions(text: str, *, self_num: str | None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in ARTICLE_MENTION_RE.finditer(text):
        num = m.group(1)
        if self_num and num == self_num:
            continue
        mention = m.group(0)
        key = mention.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(mention)
    return found


def detect_named_standards(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in NAMED_STANDARD_RE.finditer(text):
        name = m.group(1).strip().rstrip(",")
        low = name.lower()
        if low in SKIP_STANDARD_NAMES or low.startswith("this "):
            continue
        if "this standard" in low or low.endswith(" this"):
            continue
        # Named standards are titles, not clauses ("requirements detailed in Section 1").
        if re.search(r"\b(section|article|clause|comply|violat|requirement)\b", low):
            continue
        if len(name.split()) < 2 or len(name) > 80:
            continue
        if low in seen:
            continue
        seen.add(low)
        found.append(name)
    return found


def _term_present(term: str, text: str) -> bool:
    """Whole-word match; allow simple plural for capitalized glossary terms."""
    esc = re.escape(term)
    if re.search(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", text):
        return True
    if term[:1].isupper() and len(term) >= 4:
        if re.search(rf"(?<![A-Za-z0-9]){esc}s(?![A-Za-z0-9])", text):
            return True
    return False


def definitions_for_chunk(
    chunk: dict, glossary: dict[str, dict]
) -> list[dict]:
    text = chunk.get("text") or ""
    attached: list[dict] = []
    for key, entry in glossary.items():
        term = entry["term"]
        if entry["kind"] in {"acronym_expansion", "quoted_alias_phrase"}:
            continue
        if entry["kind"] == "quoted_alias" and " " in term and len(term.split()) > 4:
            continue
        if entry["source_chunk_id"] == chunk["chunk_id"]:
            continue
        if not _term_present(term, text):
            continue
        if entry["definition"][:40].lower() in text.lower():
            continue
        attached.append({
            "term": term,
            "definition": entry["definition"],
            "source_chunk_id": entry["source_chunk_id"],
            "method": "glossary_lookup",
        })
    attached.sort(key=lambda d: (len(d["term"]), d["term"].lower()))
    return attached


def _ssl_context():
    """Use certifi CA bundle when available (fixes many macOS Python installs)."""
    try:
        import certifi
        import ssl

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def call_openai_json(
    prompt: str, *, model: str = "gpt-4o-mini", retries: int = 5
) -> dict | None:
    import time

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You help resolve document cross-references. "
                    "Reply with compact JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    data = json.dumps(body).encode("utf-8")
    ctx = _ssl_context()
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            return json.loads(content)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                exc._body = detail  # type: ignore[attr-defined]
            except Exception:
                pass
            # Retry only transient rate limits — not hard quota exhaustion.
            code = None
            try:
                code = json.loads(detail or "{}").get("error", {}).get("code")
            except Exception:
                code = None
            retryable = exc.code in {500, 502, 503, 504} or (
                exc.code == 429 and code != "insufficient_quota"
            )
            if retryable and attempt < retries - 1:
                wait = min(60.0, (2 ** attempt) + 1)
                ra = exc.headers.get("Retry-After") if exc.headers else None
                if ra:
                    try:
                        wait = max(wait, float(ra))
                    except ValueError:
                        pass
                print(
                    f"OpenAI {exc.code}; retry in {wait:.0f}s "
                    f"({attempt + 1}/{retries})…",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            break
        except Exception as exc:
            last_exc = exc
            break

    if last_exc is not None and not getattr(call_openai_json, "_warned", False):
        print(
            f"OpenAI call failed ({type(last_exc).__name__}): {last_exc}",
            file=sys.stderr,
        )
        body = getattr(last_exc, "_body", None)
        if body:
            print(f"OpenAI response: {body[:500]}", file=sys.stderr)
        if "CERTIFICATE" in str(last_exc).upper() or "SSL" in type(last_exc).__name__.upper():
            print(
                "Hint: on macOS python.org installs, run:\n"
                "  /Applications/Python\\ 3.10/Install\\ Certificates.command\n"
                "Or: pip install certifi && "
                "python3 -c \"import certifi; print(certifi.where())\"",
                file=sys.stderr,
            )
        call_openai_json._warned = True
    return None


def maybe_shorten_excerpt(
    mention: str, excerpt: str, *, use_llm: bool
) -> tuple[str, str]:
    if len(excerpt) <= DEFAULT_EXCERPT_CHARS:
        return excerpt, "verbatim"
    if use_llm:
        result = call_openai_json(
            "Summarize the target section so an extraction model can apply the "
            f"cross-reference '{mention}'. Keep legal meaning. Max 80 words.\n"
            f"Return JSON: {{\"excerpt\": \"...\"}}\n\n"
            f"SECTION:\n{excerpt[:4000]}"
        )
        if result and isinstance(result.get("excerpt"), str) and result["excerpt"].strip():
            return result["excerpt"].strip(), "llm_paraphrase"
    return _clip(excerpt, DEFAULT_EXCERPT_CHARS), "truncated"


def resolve_named_with_llm(
    mention: str, candidates: list[dict], *, use_llm: bool
) -> dict | None:
    if not use_llm or not candidates:
        return None
    result = call_openai_json(
        "Pick which candidate section the mention refers to, or null if none fit.\n"
        f"Mention: {mention}\n"
        f"Candidates: {json.dumps(candidates, ensure_ascii=False)}\n"
        'Return JSON: {"heading": "..."|null, "reason": "..."}'
    )
    if not result:
        return None
    heading = result.get("heading")
    if not heading:
        return None
    for c in candidates:
        if c["heading"] == heading:
            return c
    return None


def resolve_chunk(
    chunk: dict,
    *,
    section_index: dict[str, dict],
    glossary: dict[str, dict],
    use_llm: bool = False,
) -> dict:
    text = chunk.get("text") or ""
    self_num = _self_article_num(chunk)

    refs: list[dict] = []

    for mention in detect_article_mentions(text, self_num=self_num):
        target = lookup_section(section_index, mention)
        if not target:
            refs.append({
                "mention": mention,
                "status": "unresolved",
                "method": "section_index",
            })
            continue
        excerpt, method = maybe_shorten_excerpt(
            mention, target["text"], use_llm=use_llm
        )
        refs.append({
            "mention": mention,
            "status": "resolved",
            "method": method if method != "verbatim" else "section_index",
            "target_heading": target["heading"],
            "target_breadcrumb": target.get("breadcrumb"),
            "source_chunk_ids": target["source_chunk_ids"],
            "excerpt": excerpt,
        })

    for name in detect_named_standards(text):
        mention = f"{name} Standard"
        target = lookup_section(section_index, name)
        if target:
            excerpt, method = maybe_shorten_excerpt(
                mention, target["text"], use_llm=use_llm
            )
            refs.append({
                "mention": mention,
                "status": "resolved",
                "method": method if method != "verbatim" else "section_index",
                "target_heading": target.get("toc_title") or target["heading"],
                "target_breadcrumb": target.get("breadcrumb"),
                "source_chunk_ids": target["source_chunk_ids"],
                "excerpt": excerpt,
            })
            continue

        candidates = find_ambiguous_candidates(section_index, name)
        picked = resolve_named_with_llm(mention, candidates, use_llm=use_llm)
        if picked:
            # Re-lookup full entry by heading.
            target = lookup_section(section_index, picked["heading"]) or {
                "heading": picked["heading"],
                "breadcrumb": picked.get("breadcrumb"),
                "source_chunk_ids": [],
                "text": "",
            }
            excerpt, method = maybe_shorten_excerpt(
                mention, target.get("text") or "", use_llm=use_llm
            )
            refs.append({
                "mention": mention,
                "status": "resolved",
                "method": "llm_disambiguate",
                "target_heading": target["heading"],
                "target_breadcrumb": target.get("breadcrumb"),
                "source_chunk_ids": target.get("source_chunk_ids") or [],
                "excerpt": excerpt,
                "candidates": candidates,
            })
        elif candidates:
            refs.append({
                "mention": mention,
                "status": "ambiguous",
                "method": "section_index",
                "candidates": candidates,
            })
        else:
            refs.append({
                "mention": mention,
                "status": "unresolved",
                "method": "section_index",
                "note": "target not found in this document (may be external)",
            })

    defs = definitions_for_chunk(chunk, glossary)

    out = dict(chunk)
    out["attached_definitions"] = defs
    out["resolved_references"] = refs
    out["enrichment"] = {
        "definition_count": len(defs),
        "reference_count": len(refs),
        "resolved_count": sum(1 for r in refs if r["status"] == "resolved"),
        "unresolved_count": sum(1 for r in refs if r["status"] == "unresolved"),
        "ambiguous_count": sum(1 for r in refs if r["status"] == "ambiguous"),
    }
    return out


def enrich_document(
    chunks_payload: dict,
    *,
    use_llm: bool = False,
) -> dict:
    chunks = chunks_payload["chunks"]
    doc_id = chunks_payload.get("doc_id") or (chunks[0]["doc_id"] if chunks else None)
    section_index = build_section_index(chunks)
    glossary = build_glossary(chunks)

    enriched = [
        resolve_chunk(
            ch,
            section_index=section_index,
            glossary=glossary,
            use_llm=use_llm,
        )
        for ch in chunks
    ]

    # Compact index views for inspection / eval (not full section bodies).
    glossary_view = {
        k: {
            "term": v["term"],
            "definition": v["definition"],
            "source_chunk_id": v["source_chunk_id"],
            "kind": v["kind"],
        }
        for k, v in sorted(glossary.items(), key=lambda kv: kv[0])
    }
    section_view = {}
    for key, entry in section_index.items():
        # Prefer article_N / short keys in the exported view.
        if key.startswith("article ") or key.startswith("article_"):
            export_key = key.replace(" ", "_")
        elif entry.get("toc_title"):
            export_key = _norm_key(entry["toc_title"])
        else:
            export_key = key
        if export_key in section_view:
            continue
        section_view[export_key] = {
            "heading": entry["heading"],
            "breadcrumb": entry.get("breadcrumb"),
            "source_chunk_ids": entry["source_chunk_ids"],
            "toc_title": entry.get("toc_title"),
            "char_count": len(entry.get("text") or ""),
        }

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
    }

    return {
        "doc_id": doc_id,
        "chunk_count": len(enriched),
        "glossary_size": len(glossary_view),
        "section_index_size": len(section_view),
        "totals": totals,
        "glossary": glossary_view,
        "section_index": section_view,
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
    use_llm: bool = False,
    out_path: str | Path | None = None,
) -> dict:
    chunks_path = Path(chunks_path)
    payload = load_chunks_file(chunks_path)
    enriched = enrich_document(payload, use_llm=use_llm)
    dest = Path(out_path) if out_path else ENRICHED_DIR / f"{chunks_path.stem}.json"
    write_enriched_json(enriched, dest)
    return enriched


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Resolve defs/cross-refs onto section chunks → enriched JSON."
    )
    ap.add_argument(
        "chunks",
        nargs="?",
        help="Path to one chunks JSON file. Omit to process output/chunks/*.json.",
    )
    ap.add_argument(
        "--llm",
        action="store_true",
        help="Use OpenAI for long-excerpt paraphrase / ambiguous named refs "
        "(requires OPENAI_API_KEY).",
    )
    args = ap.parse_args()

    targets = (
        [Path(args.chunks)] if args.chunks else sorted(CHUNKS_DIR.glob("*.json"))
    )
    if not targets:
        print(f"No chunk JSON found in {CHUNKS_DIR}")

    for path in targets:
        enriched = enrich_chunks_file(path, use_llm=args.llm)
        t = enriched["totals"]
        print(
            f"{path.name}: chunks={enriched['chunk_count']} "
            f"glossary={enriched['glossary_size']} "
            f"sections={enriched['section_index_size']} "
            f"defs_attached={t['definition_attachments']} "
            f"refs={t['references_detected']} "
            f"resolved={t['references_resolved']} "
            f"unresolved={t['references_unresolved']} "
            f"ambiguous={t['references_ambiguous']} "
            f"-> {ENRICHED_DIR / path.name}"
        )
