"""PDF → structured Markdown with section breadcrumbs."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input_docs"
OUTPUT_DIR = ROOT / "output" / "markdown"

BREADCRUMB_SEP = " › "
HEADING_SIZE_RATIO = 1.10
MAX_HEADING_WORDS = 14
MIN_REPEATED_PAGES = 3
PAGE_NUMBER_LINE = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?$", re.I)

COLUMN_GAP_RATIO = 0.12
MIN_COLUMN_ROWS = 2
HEADING_WRAP_MAX_GAP = 20.0

# Reject OJ dates like "4.5.2016 …" as dotted numbering.
DATE_PREFIX = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}\b")

NUMBERING_PATTERNS = [
    (1, re.compile(r"^[A-E]\.\s+\S")),                       # A. LABOR
    (2, re.compile(r"^\d+\)\s+\S")),                         # 1) …
    (3, re.compile(r"^\d+(\.\d+){2,}\s+[A-Za-z]")),          # 1.1.1 …
    (2, re.compile(r"^\d+\.\d+\s+[A-Za-z]")),                # 1.1 …
    (1, re.compile(r"^\d+\.\s+\S")),                         # 1. …
    (1, re.compile(r"^Article\s+\d+\s*$", re.I)),            # whole line only
    (1, re.compile(r"^Section\s+\d+\s*$", re.I)),
    (1, re.compile(r"^Chapter\s+\d+\s*$", re.I)),
    (1, re.compile(r"^CHAPTER\s+[IVXLC]+\b")),
    (1, re.compile(r"^Section\s+[IVXLC]+\b", re.I)),
]

LAYOUT_NOISE_PATTERNS = [
    re.compile(
        r"^\d{1,2}\.\d{1,2}\.\d{4}\s+Official Journal of the European Union\b",
        re.I,
    ),
    re.compile(
        r"^L\s+\d+/\d+\s+Official Journal of the European Union\b",
        re.I,
    ),
    re.compile(
        r"^Official Journal of the European Union\s+\d{1,2}\.\d{1,2}\.\d{4}\b",
        re.I,
    ),
    re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4},\s*p\.\s*\d+", re.I),
    re.compile(r"^L\s+\d+/\d+\s*$", re.I),
]

FALSE_HEADING_PATTERNS = [
    re.compile(r"^[IVXLC]{1,4}$"),
    re.compile(r"^[F-Z]\.\s+[A-Z]{2,}$"),  # M. SCHULZ, not A. LABOR
]

BACK_MATTER_TITLES = {
    "REFERENCES", "DOCUMENT HISTORY", "APPENDIX", "APPENDICES",
    "GLOSSARY", "ANNEX", "BIBLIOGRAPHY", "DEFINITIONS",
}

FOOTNOTE_PATTERN = re.compile(r"^(\d{1,2})\s+(?=[A-Z])")
INCOMPLETE_END = re.compile(r'[.!?]"?$|:$')
HEADER_NOISE = re.compile(r"^\d{1,2}$")

DOC_LABEL_OVERRIDES = {
    "rba_code_of_conduct": "RBA",
    "apple_suppliercode_responsibility": "APPLE",
    "gdpr": "GDPR",
}


def friendly_doc_label(pdf_path: Path) -> str:
    key = pdf_path.stem.lower()
    return DOC_LABEL_OVERRIDES.get(key, pdf_path.stem.upper())


def _raw_fragments(pdf_lines: list[dict]) -> list[dict]:
    frags: list[dict] = []
    for line in pdf_lines:
        spans = line.get("spans") or []
        if not spans:
            continue
        text = "".join(s["text"] for s in spans)
        if not text.strip():
            continue
        x0, y0, x1, _ = line["bbox"]
        frags.append({
            "text": text,
            "x0": x0,
            "x1": x1,
            "y0": round(y0, 1),
            "sizes": [round(s["size"], 1) for s in spans],
            "colors": [s["color"] for s in spans],
            "font": spans[0]["font"],
        })
    return frags


def _detect_column_split_by_rows(frags: list[dict], page_width: float) -> float | None:
    """Split on wide same-row gaps; needs MIN_COLUMN_ROWS gutter rows."""
    by_y0: dict[float, list[dict]] = defaultdict(list)
    for f in frags:
        by_y0[f["y0"]].append(f)

    gap_threshold = page_width * COLUMN_GAP_RATIO
    split_points: list[float] = []

    for row in by_y0.values():
        if len(row) < 2:
            continue
        row_sorted = sorted(row, key=lambda f: f["x0"])
        row_splits = [
            (a["x1"] + b["x0"]) / 2
            for a, b in zip(row_sorted, row_sorted[1:])
            if b["x0"] - a["x1"] >= gap_threshold
        ]
        if row_splits:
            best_a_b = max(
                (
                    (b["x0"] - a["x1"], (a["x1"] + b["x0"]) / 2)
                    for a, b in zip(row_sorted, row_sorted[1:])
                    if b["x0"] - a["x1"] >= gap_threshold
                ),
                key=lambda t: t[0],
            )
            split_points.append(best_a_b[1])

    if len(split_points) < MIN_COLUMN_ROWS:
        return None

    split_points.sort()
    return split_points[len(split_points) // 2]


def _detect_column_split_by_x_clusters(frags: list[dict], page_width: float) -> float | None:
    """Fallback split when left/right columns rarely share a y0."""
    if len(frags) < 8:
        return None

    xs = sorted({round(f["x0"], 0) for f in frags})
    if len(xs) < 4:
        return None

    center_lo = page_width * 0.30
    center_hi = page_width * 0.70
    gap_threshold = page_width * COLUMN_GAP_RATIO

    best: tuple[float, float] | None = None
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        split_x = (a + b) / 2
        if gap < gap_threshold or not (center_lo <= split_x <= center_hi):
            continue
        left_n = sum(1 for f in frags if f["x0"] < split_x)
        right_n = sum(1 for f in frags if f["x0"] >= split_x)
        if left_n < 3 or right_n < 3:
            continue
        if best is None or gap > best[0]:
            best = (gap, split_x)

    return best[1] if best else None


def _detect_column_split(frags: list[dict], page_width: float) -> float | None:
    if not frags:
        return None
    return (
        _detect_column_split_by_rows(frags, page_width)
        or _detect_column_split_by_x_clusters(frags, page_width)
    )


def _merge_same_row(frags: list[dict]) -> list[dict]:
    by_y0: dict[float, list[dict]] = defaultdict(list)
    order: list[float] = []
    for f in frags:
        if f["y0"] not in by_y0:
            order.append(f["y0"])
        by_y0[f["y0"]].append(f)

    merged: list[dict] = []
    for y0 in order:
        parts = sorted(by_y0[y0], key=lambda f: f["x0"])
        text = "".join(p["text"] for p in parts).strip()
        if not text:
            continue
        sizes = [s for p in parts for s in p["sizes"]]
        colors = [c for p in parts for c in p["colors"]]
        merged.append({
            "text": text,
            "size": max(sizes),
            "color": Counter(colors).most_common(1)[0][0],
            "font": parts[0]["font"],
            "y0": y0,
        })
    return merged


def _merge_page_fragments(frags: list[dict], page_width: float) -> list[dict]:
    split_x = _detect_column_split(frags, page_width)
    if split_x is None:
        return _merge_same_row(frags)

    left = [f for f in frags if f["x0"] < split_x]
    right = [f for f in frags if f["x0"] >= split_x]
    return _merge_same_row(left) + _merge_same_row(right)


def extract_merged_lines(pdf_path: str | Path) -> list[dict]:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"no such file: '{pdf_path}'")

    doc = fitz.open(pdf_path)
    lines_out: list[dict] = []

    for page_num, page in enumerate(doc):
        page_width = page.rect.width
        text_dict = page.get_text("dict")
        page_pdf_lines: list[dict] = []
        for block in text_dict["blocks"]:
            if "lines" in block:
                page_pdf_lines.extend(block["lines"])

        frags = _raw_fragments(page_pdf_lines)
        for line in _merge_page_fragments(frags, page_width):
            lines_out.append({**line, "page": page_num + 1})

    return lines_out


def detect_repeated_noise_lines(lines: list[dict]) -> set[str]:
    pages_per_text: dict[str, set[int]] = defaultdict(set)
    for line in lines:
        pages_per_text[line["text"].strip()].add(line["page"])
    return {t for t, pages in pages_per_text.items() if len(pages) >= MIN_REPEATED_PAGES}


def detect_body_size(lines: list[dict]) -> float:
    weighted = Counter()
    for line in lines:
        weighted[line["size"]] += len(line["text"])
    return weighted.most_common(1)[0][0]


def _looks_like_heading_text(text: str) -> bool:
    return len(text.split()) <= MAX_HEADING_WORDS


def is_layout_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    return any(p.search(t) for p in LAYOUT_NOISE_PATTERNS)


def is_false_heading(text: str) -> bool:
    t = text.strip()
    return any(p.match(t) for p in FALSE_HEADING_PATTERNS)


def collect_heading_sizes(lines: list[dict], body_size: float, noise: set[str]) -> list[float]:
    sizes: set[float] = set()
    for line in lines:
        text = line["text"].strip()
        if not text or text in noise or is_layout_noise(text) or is_false_heading(text):
            continue
        if line["size"] < body_size * HEADING_SIZE_RATIO:
            continue
        if not _looks_like_heading_text(text):
            continue
        sizes.add(line["size"])
    return sorted(sizes, reverse=True)


def build_level_map(heading_sizes: list[float]) -> dict[float, int]:
    return {size: rank + 1 for rank, size in enumerate(heading_sizes)}


def _numbering_match(text: str) -> tuple[int, re.Match] | None:
    if DATE_PREFIX.match(text):
        return None
    for level, pattern in NUMBERING_PATTERNS:
        m = pattern.match(text)
        if m:
            return level, m
    return None


def classify_line(
    line: dict,
    *,
    body_size: float,
    level_map: dict[float, int],
    noise_lines: set[str],
) -> dict | None:
    text = line["text"].strip()
    if (
        not text
        or text in noise_lines
        or PAGE_NUMBER_LINE.match(text)
        or is_layout_noise(text)
        or HEADER_NOISE.match(text)
    ):
        return None

    size = line["size"]
    page = line["page"]
    y0 = line.get("y0")

    is_heading_size = size >= body_size * HEADING_SIZE_RATIO
    numbering = _numbering_match(text)

    if (is_heading_size or numbering) and _looks_like_heading_text(text):
        if is_false_heading(text):
            return {"type": "paragraph", "text": text, "page": page, "size": size}
        if numbering:
            level = numbering[0]
        else:
            level = level_map.get(size, len(level_map) + 1)
        return {"type": "heading", "level": level, "text": text, "page": page, "y0": y0, "size": size}

    footnote_match = FOOTNOTE_PATTERN.match(text)
    if footnote_match and not numbering:
        return {"type": "footnote", "number": int(footnote_match.group(1)), "text": text, "page": page}

    return {"type": "paragraph", "text": text, "page": page, "size": size}


def merge_wrapped_headings(blocks: list[dict]) -> list[dict]:
    if not blocks:
        return []

    out: list[dict] = []
    buf: dict | None = None

    def flush() -> None:
        nonlocal buf
        if buf is not None:
            out.append(buf)
            buf = None

    for block in blocks:
        if block["type"] != "heading":
            flush()
            out.append(block)
            continue

        if buf is None:
            buf = dict(block)
            continue

        same_page = buf["page"] == block["page"]
        same_level = buf.get("level") == block.get("level")
        y_gap = None
        if buf.get("y0") is not None and block.get("y0") is not None:
            y_gap = abs(block["y0"] - buf["y0"])

        if same_page and same_level and y_gap is not None and y_gap <= HEADING_WRAP_MAX_GAP:
            buf["text"] = f"{buf['text'].rstrip()} {block['text'].lstrip()}"
            buf["y0"] = block["y0"]
        else:
            flush()
            buf = dict(block)

    flush()
    return out


def _paragraph_incomplete(text: str) -> bool:
    t = text.rstrip()
    if not t:
        return True
    return INCOMPLETE_END.search(t) is None


def merge_blocks(blocks: list[dict]) -> list[dict]:
    if not blocks:
        return []

    out: list[dict] = []
    para_buf: dict | None = None
    footnote_buf: dict | None = None

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf is not None:
            out.append(para_buf)
            para_buf = None

    def flush_footnote() -> None:
        nonlocal footnote_buf
        if footnote_buf is not None:
            out.append(footnote_buf)
            footnote_buf = None

    for block in blocks:
        if block["type"] == "heading":
            flush_para()
            flush_footnote()
            out.append(block)
            continue

        if block["type"] == "footnote":
            flush_para()
            flush_footnote()
            footnote_buf = dict(block)
            continue

        if footnote_buf is not None:
            footnote_buf["text"] = f"{footnote_buf['text'].rstrip()} {block['text'].lstrip()}"
            continue

        if para_buf is None:
            para_buf = dict(block)
            continue

        same_page = para_buf["page"] == block["page"]
        if same_page or _paragraph_incomplete(para_buf["text"]):
            para_buf["text"] = f"{para_buf['text'].rstrip()} {block['text'].lstrip()}"
        else:
            flush_para()
            para_buf = dict(block)

    flush_para()
    flush_footnote()
    return out


def attach_breadcrumbs(blocks: list[dict], *, doc_label: str, sep: str = BREADCRUMB_SEP) -> list[dict]:
    stack: dict[int, str] = {}
    enriched: list[dict] = []

    for block in blocks:
        b = dict(block)
        if b["type"] == "heading":
            level = b.get("level", 1)
            title = b["text"].strip()
            if title.upper() in BACK_MATTER_TITLES:
                stack = {1: title}
            else:
                stack[level] = title
                for deeper in [lv for lv in stack if lv > level]:
                    del stack[deeper]

        parts = [doc_label] + [stack[lv] for lv in sorted(stack)]
        b["breadcrumb"] = sep.join(parts)
        enriched.append(b)

    return enriched


def blocks_to_markdown(blocks: list[dict], *, doc_label: str) -> str:
    lines: list[str] = [
        f"# {doc_label} — structured extract",
        "",
        "Generated by `src/parser.py`. Each block has one `breadcrumb` comment.",
        "",
    ]

    for block in blocks:
        crumb = block.get("breadcrumb", doc_label)
        text = block["text"].strip()
        page = block.get("page")

        lines.append(f"<!-- breadcrumb: {crumb} | page: {page} -->")

        if block["type"] == "heading":
            level = block.get("level", 1)
            md_level = min(2 + (level - 1), 6)
            lines.append(f"{'#' * md_level} {text}")
            lines.append("")
        elif block["type"] == "footnote":
            lines.append(f"> **Footnote {block.get('number')}:** {text}")
            lines.append("")
        else:
            lines.append(text)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_document(pdf_path: str | Path, *, doc_label: str | None = None) -> list[dict]:
    pdf_path = Path(pdf_path)
    doc_label = doc_label or friendly_doc_label(pdf_path)

    raw_lines = extract_merged_lines(pdf_path)
    noise_lines = detect_repeated_noise_lines(raw_lines)
    body_size = detect_body_size(raw_lines)
    heading_sizes = collect_heading_sizes(raw_lines, body_size, noise_lines)
    level_map = build_level_map(heading_sizes)

    classified: list[dict] = []
    for line in raw_lines:
        result = classify_line(line, body_size=body_size, level_map=level_map, noise_lines=noise_lines)
        if result is not None:
            classified.append(result)

    wrapped = merge_wrapped_headings(classified)
    merged = merge_blocks(wrapped)
    return attach_breadcrumbs(merged, doc_label=doc_label)


def write_markdown(pdf_path: str | Path, out_path: str | Path | None = None, *, doc_label: str | None = None) -> Path:
    pdf_path = Path(pdf_path)
    doc_label = doc_label or friendly_doc_label(pdf_path)
    out_path = Path(out_path) if out_path else OUTPUT_DIR / f"{pdf_path.stem}.md"

    blocks = parse_document(pdf_path, doc_label=doc_label)
    md = blocks_to_markdown(blocks, doc_label=doc_label)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Parse a compliance PDF into breadcrumb-tagged Markdown.")
    ap.add_argument("pdf", nargs="?", help="Path to a single PDF. Omit to process every PDF in input_docs/.")
    ap.add_argument("--label", help="Override document label used in breadcrumbs (e.g. GDPR).")
    args = ap.parse_args()

    targets = [Path(args.pdf)] if args.pdf else sorted(INPUT_DIR.glob("*.pdf"))
    if not targets:
        print(f"No PDFs found in {INPUT_DIR}")

    for pdf_path in targets:
        label = args.label or friendly_doc_label(pdf_path)
        blocks = parse_document(pdf_path, doc_label=label)
        out = write_markdown(pdf_path, doc_label=label)
        headings = sum(1 for b in blocks if b["type"] == "heading")
        paras = sum(1 for b in blocks if b["type"] == "paragraph")
        footnotes = sum(1 for b in blocks if b["type"] == "footnote")
        levels = sorted({b["level"] for b in blocks if b["type"] == "heading"})
        print(
            f"{pdf_path.name}: blocks={len(blocks)} headings={headings} "
            f"paragraphs={paras} footnotes={footnotes} heading_levels={levels} -> {out}"
        )
