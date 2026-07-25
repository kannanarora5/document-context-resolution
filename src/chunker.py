"""Section-boundary chunker for parser Markdown output.

Splits on headings (not fixed token count). Each chunk keeps its breadcrumb
and optional surrounding context from the previous chunk.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_DIR = ROOT / "output" / "markdown"
CHUNKS_DIR = ROOT / "output" / "chunks"

BREADCRUMB_RE = re.compile(
    r"^<!--\s*breadcrumb:\s*(.*?)\s*\|\s*page:\s*(\d+|None)\s*-->\s*$"
)
HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")

# Trailing chars from the previous chunk attached as surrounding context.
DEFAULT_CONTEXT_CHARS = 400
# Soft cap: oversized sections (e.g. GDPR recitals) split on paragraph breaks.
DEFAULT_MAX_CHARS = 6000


def parse_markdown_blocks(md_text: str) -> list[dict]:
    """Turn breadcrumb-tagged Markdown into ordered content blocks."""
    lines = md_text.splitlines()
    blocks: list[dict] = []
    i = 0
    n = len(lines)

    while i < n:
        m = BREADCRUMB_RE.match(lines[i])
        if not m:
            i += 1
            continue

        breadcrumb = m.group(1).strip()
        page_raw = m.group(2)
        page = None if page_raw == "None" else int(page_raw)
        i += 1

        # Skip blank lines between the comment and the content.
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break

        content_line = lines[i]
        i += 1

        hm = HEADING_RE.match(content_line)
        if hm:
            level = len(hm.group(1)) - 1  # ## → 1, ### → 2, …
            blocks.append({
                "type": "heading",
                "level": level,
                "text": hm.group(2).strip(),
                "breadcrumb": breadcrumb,
                "page": page,
            })
            continue

        # Gather continuation lines until the next breadcrumb comment.
        parts = [content_line]
        while i < n and not BREADCRUMB_RE.match(lines[i]):
            parts.append(lines[i])
            i += 1
        text = "\n".join(parts).strip()
        if not text:
            continue

        if text.startswith("> **Footnote"):
            btype = "footnote"
        else:
            btype = "paragraph"
        blocks.append({
            "type": btype,
            "text": text,
            "breadcrumb": breadcrumb,
            "page": page,
        })

    return blocks


def _split_oversized_text(text: str, max_chars: int) -> list[str]:
    """Split text into pieces <= max_chars, preferring paragraph boundaries."""
    text = text.strip()
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [text]

    parts: list[str] = []
    buf = ""

    def push_buf() -> None:
        nonlocal buf
        if buf.strip():
            parts.append(buf.strip())
        buf = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Giant single paragraph: hard-split near max_chars on whitespace.
        if len(para) > max_chars:
            push_buf()
            start = 0
            while start < len(para):
                end = min(start + max_chars, len(para))
                if end < len(para):
                    cut = para.rfind(" ", start, end)
                    if cut > start:
                        end = cut
                piece = para[start:end].strip()
                if piece:
                    parts.append(piece)
                start = end
                while start < len(para) and para[start].isspace():
                    start += 1
            continue

        candidate = f"{buf}\n\n{para}".strip() if buf else para
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            push_buf()
            buf = para

    push_buf()
    return parts or [text]


def _expand_by_max_chars(chunks: list[dict], max_chars: int) -> list[dict]:
    """Split any section whose text exceeds max_chars into sibling parts."""
    if max_chars <= 0:
        return chunks

    expanded: list[dict] = []
    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= max_chars:
            expanded.append(chunk)
            continue

        parts = _split_oversized_text(text, max_chars)
        total = len(parts)
        for i, part in enumerate(parts, start=1):
            piece = dict(chunk)
            piece["text"] = part
            piece["char_count"] = len(part)
            piece["part"] = i
            piece["part_count"] = total
            expanded.append(piece)
    return expanded


def chunk_blocks(
    blocks: list[dict],
    *,
    doc_id: str,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    """One chunk per heading section; oversized sections split on paragraphs."""
    if not blocks:
        return []

    chunks: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        text = current["text"].strip()
        # Drop empty preamble noise, but keep heading-only sections that
        # still carry structure (e.g. a chapter title with no body yet).
        if not text and not current.get("heading"):
            current = None
            return
        current["text"] = text
        current["char_count"] = len(text)
        current["part"] = 1
        current["part_count"] = 1
        chunks.append(current)
        current = None

    for block in blocks:
        if block["type"] == "heading":
            flush()
            pages = [block["page"]] if block.get("page") is not None else []
            current = {
                "chunk_id": "",  # filled after flush ordering
                "doc_id": doc_id,
                "breadcrumb": block["breadcrumb"],
                "heading": block["text"],
                "level": block["level"],
                "pages": pages,
                "text": block["text"],
            }
            continue

        if current is None:
            # Leading body before the first heading.
            pages = [block["page"]] if block.get("page") is not None else []
            current = {
                "chunk_id": "",
                "doc_id": doc_id,
                "breadcrumb": block.get("breadcrumb", doc_id),
                "heading": None,
                "level": None,
                "pages": pages,
                "text": block["text"],
            }
            continue

        current["text"] = f"{current['text'].rstrip()}\n\n{block['text'].lstrip()}"
        page = block.get("page")
        if page is not None and page not in current["pages"]:
            current["pages"].append(page)

    flush()
    chunks = _expand_by_max_chars(chunks, max_chars)

    # Fill ids + surrounding context (tail of previous chunk).
    prev_text = ""
    for idx, chunk in enumerate(chunks, start=1):
        chunk["chunk_id"] = f"{doc_id}-{idx:04d}"
        if context_chars > 0 and prev_text:
            tail = prev_text[-context_chars:].lstrip()
            chunk["context_before"] = tail
        else:
            chunk["context_before"] = ""
        prev_text = chunk["text"]

    return chunks


def chunk_markdown_file(
    md_path: str | Path,
    *,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    md_path = Path(md_path)
    doc_id = md_path.stem
    blocks = parse_markdown_blocks(md_path.read_text(encoding="utf-8"))
    return chunk_blocks(
        blocks,
        doc_id=doc_id,
        context_chars=context_chars,
        max_chars=max_chars,
    )


def write_chunks_json(chunks: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "doc_id": chunks[0]["doc_id"] if chunks else None,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Chunk structured Markdown by section boundaries.")
    ap.add_argument(
        "markdown",
        nargs="?",
        help="Path to one .md file. Omit to process every file in output/markdown/.",
    )
    ap.add_argument(
        "--context-chars",
        type=int,
        default=DEFAULT_CONTEXT_CHARS,
        help="Chars of previous-chunk text to attach as context_before (default 400).",
    )
    ap.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Soft max chars per chunk; oversized sections split on paragraphs (default 6000).",
    )
    args = ap.parse_args()

    targets = [Path(args.markdown)] if args.markdown else sorted(MARKDOWN_DIR.glob("*.md"))
    if not targets:
        print(f"No Markdown found in {MARKDOWN_DIR}")

    for md_path in targets:
        chunks = chunk_markdown_file(
            md_path,
            context_chars=args.context_chars,
            max_chars=args.max_chars,
        )
        out = write_chunks_json(chunks, CHUNKS_DIR / f"{md_path.stem}.json")
        avg = (sum(c["char_count"] for c in chunks) / len(chunks)) if chunks else 0
        mx = max((c["char_count"] for c in chunks), default=0)
        print(
            f"{md_path.name}: chunks={len(chunks)} avg_chars={avg:.0f} "
            f"max_chars={mx} -> {out}"
        )
