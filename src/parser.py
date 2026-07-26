"""PDF -> structured Markdown with section breadcrumbs."""

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
HEADING_WRAP_SIZE_FACTOR = 1.15

# Reject OJ dates like "4.5.2016 …" as dotted numbering.
DATE_PREFIX = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}\b")

# GDPR recitals / Art. 4 definitions: "(1) …", "(51) …"; allow missing space after ")".
PAREN_UNIT_RE = re.compile(r"^\((\d+)\)(?:\s*(.*))?$")
# Apple mini-TOC items: "1. Company Statement"
DOTTED_ITEM_RE = re.compile(r"^(\d+)\.\s+\S")
# True OJ footnote cites only — do NOT flag recitals that mention Directive/Regulation.
PAREN_FOOTNOTE_RE = re.compile(r"^\(\d+\)\s*OJ\b", re.I)

NUMBERING_PATTERNS = [
    (1, re.compile(r"^[A-E]\.\s+\S")),                       # A. LABOR
    (2, re.compile(r"^\d+\)\s+\S")),                         # 1) …
    (3, re.compile(r"^\d+(\.\d+){2,}\s+[A-Za-z]")),          # 1.1.1 …
    (2, re.compile(r"^\d+\.\d+\s+[A-Za-z]")),                # 1.1 …
    (1, re.compile(r"^\d+\.\s+\S")),                         # 1. …
    # Parenthetical units handled in classify_line (recital/def path), not here
    # otherwise OJ footnotes like "(1) OJ C …" become false headings.
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
    re.compile(r"^Effective\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\s*Version\s+\d+\S*$", re.I),
    re.compile(r"^Version\s+\d+\S*\s*Effective\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}$", re.I),
    re.compile(r"^Effective\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}$", re.I),
    re.compile(r"^Version\s+\d+\S*$", re.I),
]

# Leading footer stamp glued onto the next paragraph on cover/Standards pages.
LEADING_DOC_STAMP = re.compile(
    r"^Effective\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\s*Version\s+\d+\S*\s*"
    r"|^Version\s+\d+\S*\s*Effective\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\s*"
    r"|^Effective\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\s+",
    re.I,
)

BULLET_PREFIX = re.compile(r"^[•\u2022▪▸►\-]\s*")

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
    for f in frags:
        by_y0[f["y0"]].append(f)

    # Top-to-bottom within a column. PDF block order is often not reading order
    merged: list[dict] = []
    for y0 in sorted(by_y0):
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
    """Running headers/footers that repeat across pages."""
    pages_per_text: dict[str, set[int]] = defaultdict(set)
    for line in lines:
        text = _norm_text(line["text"])
        if not text or not _eligible_for_noise(text):
            continue
        pages_per_text[text].add(line["page"])
        key = _noise_lookup_key(text)
        if key != text and _eligible_for_noise(key):
            pages_per_text[key].add(line["page"])
    return {t for t, pages in pages_per_text.items() if len(pages) >= MIN_REPEATED_PAGES}


def _eligible_for_noise(text: str) -> bool:
    """Exclude structural numbering from the repeated-line noise filter."""
    if DOTTED_ITEM_RE.match(text):
        return False
    if re.match(r"^\d+\.\d+\s+\S", text):
        return False
    if PAREN_UNIT_RE.match(text):
        return False
    if re.match(r"^Article\s+\d+\s*$", text, re.I):
        return False
    return True


def detect_body_size(lines: list[dict]) -> float:
    weighted = Counter()
    for line in lines:
        weighted[line["size"]] += len(line["text"])
    return weighted.most_common(1)[0][0]


def _norm_text(text: str) -> str:
    """Collapse tabs/spaces so breadcrumbs and section-index keys stay stable"""
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_heading_text(text: str) -> bool:
    return len(text.split()) <= MAX_HEADING_WORDS


def _first_alpha(text: str) -> str:
    for ch in text:
        if ch.isalpha():
            return ch
    return ""


def _is_plausible_title(text: str) -> bool:
    """Reject pull-quotes / mid-sentence wraps promoted by large body fonts"""
    t = text.strip()
    if not t:
        return False
    if re.match(r"^Effective\b", t, re.I):
        return False
    if re.search(r"Version\s+\d\S*.*Effective|Effective.*Version\s+\d", t, re.I):
        return False
    if re.match(r"^Version\s+\d", t, re.I):
        return False
    first = _first_alpha(t)
    if first and first.islower():
        return False
    # Trailing comma: allow short wrap midpoints but reject long sentence fragments that end mid-clause
    if t.endswith((",", ";", "—", "–")):
        if t.endswith(",") and len(t.split()) <= 8:
            return True
        return False
    # Display pull-quotes are full sentences ending in "."
    if t.endswith(".") and len(t.split()) >= 6:
        return False
    return True


def _noise_lookup_key(text: str) -> str:
    """Sidebar/TOC may be plain, bullet-prefixed, or title+page-number."""
    t = BULLET_PREFIX.sub("", text).strip()
    t = re.sub(r"[\t ]+\d{1,3}$", "", t).strip()
    return t


def _part_title_set(noise_lines: set[str]) -> set[str]:
    """Sidebar TOC strings that also label real Standard/part titles."""
    keys = {_noise_lookup_key(t) for t in noise_lines}
    return {t for t in keys if _is_plausible_title(t)}


def is_layout_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    return any(p.search(t) for p in LAYOUT_NOISE_PATTERNS)


def _strip_leading_doc_stamp(text: str) -> str:
    t = text.strip()
    if not t:
        return t
    return LEADING_DOC_STAMP.sub("", t, count=1).strip()


def is_false_heading(text: str) -> bool:
    t = text.strip()
    return any(p.match(t) for p in FALSE_HEADING_PATTERNS)


def _paren_unit_match(text: str) -> re.Match | None:
    m = PAREN_UNIT_RE.match(text)
    if not m:
        return None
    rest = (m.group(2) or "").strip()
    # "(2) and (3) …" mid-list fragment — not a new structural unit.
    if rest and rest[:1].islower() and not rest.startswith(("'", "‘", "“")):
        return None
    if PAREN_FOOTNOTE_RE.match(text):
        return None
    return m


def _is_sidebar_noise(text: str, size: float, body_size: float, noise: set[str]) -> bool:
    """Repeated headers/TOCs are noise only at body-or-smaller size"""
    if size >= body_size * HEADING_SIZE_RATIO:
        return False
    key = _noise_lookup_key(text)
    if text in noise or key in noise or f"• {key}" in noise:
        return True
    for n in noise:
        if len(n) < 10:
            continue
        if key == n + n or key.replace(" ", "") == (n + n).replace(" ", ""):
            return True
    return False


def collect_heading_sizes(lines: list[dict], body_size: float, noise: set[str]) -> list[float]:
    sizes: set[float] = set()
    for line in lines:
        text = _norm_text(line["text"])
        if not text or is_layout_noise(text) or is_false_heading(text):
            continue
        if _is_sidebar_noise(text, line["size"], body_size, noise):
            continue
        if line["size"] < body_size * HEADING_SIZE_RATIO:
            continue
        if not _looks_like_heading_text(text) or not _is_plausible_title(text):
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
) -> list[dict] | None:
    """Classify one PDF line into zero or more blocks"""
    text = _strip_leading_doc_stamp(_norm_text(line["text"]))
    size = line["size"]
    page = line["page"]
    y0 = line.get("y0")

    if (
        not text
        or _is_sidebar_noise(text, size, body_size, noise_lines)
        or PAGE_NUMBER_LINE.match(text)
        or is_layout_noise(text)
        or HEADER_NOISE.match(text)
    ):
        return None

    # Parenthetical units: body-sized only, short heading label
    paren = _paren_unit_match(text)
    if paren and size >= body_size * 0.98:
        num = paren.group(1)
        rest = (paren.group(2) or "").strip()
        blocks = [{
            "type": "heading",
            "level": 2,
            "text": f"({num})",
            "page": page,
            "y0": y0,
            "size": size,
        }]
        if rest:
            blocks.append({
                "type": "paragraph",
                "text": rest,
                "page": page,
                "y0": y0,
                "size": size,
            })
        return blocks

    is_heading_size = size >= body_size * HEADING_SIZE_RATIO
    numbering = _numbering_match(text)

    if (is_heading_size or numbering) and _looks_like_heading_text(text):
        if is_false_heading(text):
            return [{"type": "paragraph", "text": text, "page": page, "y0": y0, "size": size}]
        # Numbered section openers keep structure even when phrasing is sentence-like
        if not numbering and not _is_plausible_title(text):
            return [{"type": "paragraph", "text": text, "page": page, "y0": y0, "size": size}]
        if numbering:
            level = numbering[0]
        else:
            level = level_map.get(size, len(level_map) + 1)
        return [{
            "type": "heading",
            "level": level,
            "text": text,
            "page": page,
            "y0": y0,
            "size": size,
        }]

    footnote_match = FOOTNOTE_PATTERN.match(text)
    if footnote_match and not numbering:
        return [{
            "type": "footnote",
            "number": int(footnote_match.group(1)),
            "text": text,
            "page": page,
        }]

    return [{"type": "paragraph", "text": text, "page": page, "y0": y0, "size": size}]


def _heading_wrap_gap(buf: dict, block: dict) -> float:
    size = max(float(buf.get("size") or 0), float(block.get("size") or 0))
    return max(HEADING_WRAP_MAX_GAP, size * HEADING_WRAP_SIZE_FACTOR)


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

        max_gap = _heading_wrap_gap(buf, block)
        if same_page and same_level and y_gap is not None and y_gap <= max_gap:
            buf["text"] = _norm_text(f"{buf['text']} {block['text']}")
            buf["y0"] = block["y0"]
        else:
            flush()
            buf = dict(block)

    flush()
    return out


def absorb_heading_continuations(blocks: list[dict]) -> list[dict]:
    """Merge a same-page, same-size lowercase wrap line into the prior heading.
    """
    if not blocks:
        return []

    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b["type"] != "heading":
            out.append(b)
            i += 1
            continue

        merged = dict(b)
        j = i + 1
        while j < len(blocks):
            nxt = blocks[j]
            if nxt["type"] != "paragraph":
                break
            if nxt.get("page") != merged.get("page"):
                break
            if abs(float(nxt.get("size") or 0) - float(merged.get("size") or 0)) > 0.6:
                break
            cont = _norm_text(nxt["text"])
            first = _first_alpha(cont)
            if not first or not first.islower():
                break
            words = len(cont.split())
            if words > 8:
                break
            y_gap = None
            if merged.get("y0") is not None and nxt.get("y0") is not None:
                y_gap = abs(float(nxt["y0"]) - float(merged["y0"]))
            max_gap = _heading_wrap_gap(merged, nxt)
            if words <= 5:
                max_gap = max(max_gap, float(merged.get("size") or 0) * 1.6)
            if y_gap is None or y_gap > max_gap:
                break
            merged["text"] = _norm_text(f"{merged['text']} {cont}")
            if nxt.get("y0") is not None:
                merged["y0"] = nxt["y0"]
            j += 1

        out.append(merged)
        i = j if j > i + 1 else i + 1

    return out


def demote_implausible_headings(blocks: list[dict]) -> list[dict]:
    """After wraps, demote unnumbered headings that look like sentences."""
    out: list[dict] = []
    for b in blocks:
        if b["type"] != "heading":
            out.append(b)
            continue
        text = _norm_text(b["text"])
        if _numbering_match(text) or _paren_unit_match(text) or _is_plausible_title(text):
            out.append(b)
            continue
        para = dict(b)
        para["type"] = "paragraph"
        para.pop("level", None)
        out.append(para)
    return out


def drop_redundant_display_titles(
    blocks: list[dict],
    *,
    part_titles: set[str],
) -> list[dict]:
    """Drop thematic cover titles that sit above the sidebar-canonical Standard."""
    if not part_titles or not blocks:
        return blocks

    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if (
            b["type"] == "heading"
            and i + 1 < len(blocks)
            and blocks[i + 1]["type"] == "heading"
            and b.get("page") == blocks[i + 1].get("page")
        ):
            cur = _norm_text(b["text"])
            nxt = _norm_text(blocks[i + 1]["text"])
            if (
                not _numbering_match(cur)
                and not _numbering_match(nxt)
                and nxt in part_titles
                and cur not in part_titles
            ):
                i += 1
                continue
        out.append(b)
        i += 1
    return out


def strip_toc_body(blocks: list[dict]) -> list[dict]:
    """Keep the TOC heading; drop the navigational title/page-number rows under it."""
    out: list[dict] = []
    in_toc = False
    for b in blocks:
        if b["type"] == "heading":
            title = _norm_text(b["text"]).casefold()
            if title in {"table of contents", "contents"}:
                in_toc = True
                out.append(b)
                continue
            in_toc = False
            out.append(b)
            continue
        if in_toc:
            continue
        out.append(b)
    return out


def relevel_numbered_headings(
    blocks: list[dict],
    *,
    part_titles: set[str] | None = None,
) -> list[dict]:
    """Nest numbered sections under the latest major display."""
    part_titles = part_titles or set()
    out: list[dict] = []
    last_display_level = 0
    last_anchor_level = 0  # last non-paren heading; paren units nest under this
    for b in blocks:
        if b["type"] != "heading":
            out.append(b)
            continue
        nb = dict(b)
        text = _norm_text(nb["text"])
        numbering = _numbering_match(text)
        if numbering:
            depth = numbering[0]
            nb["level"] = last_display_level + depth
            last_anchor_level = nb["level"]
        elif _paren_unit_match(text):
            nb["level"] = last_anchor_level + 1
        else:
            level = int(nb.get("level") or 1)
            if level <= 1 or text in part_titles:
                last_display_level = level
            last_anchor_level = int(nb.get("level") or 1)
        out.append(nb)
    return out


def strip_preview_indexes(blocks: list[dict]) -> list[dict]:
    if not blocks:
        return []

    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b["type"] != "heading" or not DOTTED_ITEM_RE.match(b["text"]):
            out.append(b)
            i += 1
            continue

        j = i
        run: list[dict] = []
        while (
            j < len(blocks)
            and blocks[j]["type"] == "heading"
            and DOTTED_ITEM_RE.match(blocks[j]["text"])
        ):
            run.append(blocks[j])
            j += 1

        # Split on restarts: … 12. Foo, 1. Foo again … or short intro TOCs
        # like "1. TPEA … 2. TPEA …" then real "1. TPEA …" on the next page.
        segs: list[list[dict]] = []
        start = 0
        for k in range(1, len(run)):
            prev_n = int(DOTTED_ITEM_RE.match(run[k - 1]["text"]).group(1))
            n = int(DOTTED_ITEM_RE.match(run[k]["text"]).group(1))
            if n == 1 and prev_n >= 2:
                segs.append(run[start:k])
                start = k
        segs.append(run[start:])

        next_after = blocks[j] if j < len(blocks) else None
        for s_i, seg in enumerate(segs):
            is_last = s_i == len(segs) - 1
            nums = [int(DOTTED_ITEM_RE.match(x["text"]).group(1)) for x in seg]
            sequential = nums == list(range(nums[0], nums[0] + len(nums)))

            if not is_last and len(seg) >= 2:
                continue
            if (
                is_last
                and len(seg) >= 4
                and sequential
                and nums[0] == 1
                and next_after is not None
                and next_after["type"] == "paragraph"
            ):
                out.append(seg[0])
                continue

            # Final segment with no following body -> pure preview list.
            if is_last and len(seg) >= 4 and (
                next_after is None or next_after["type"] != "paragraph"
            ):
                continue

            out.extend(seg)
        i = j

    return out


def dedupe_preview_section_openers(blocks: list[dict]) -> list[dict]:
    """Drop a numbered opener kept from a mini-TOC when the real opener follows."""
    if not blocks:
        return []

    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b["type"] != "heading" or not DOTTED_ITEM_RE.match(b["text"]):
            out.append(b)
            i += 1
            continue

        title = _norm_text(b["text"])
        j = i + 1
        only_soft = True
        restart_at = None
        while j < len(blocks):
            nxt = blocks[j]
            if nxt["type"] == "heading":
                if _norm_text(nxt["text"]) == title and DOTTED_ITEM_RE.match(nxt["text"]):
                    restart_at = j
                break
            if nxt["type"] not in ("paragraph", "footnote"):
                only_soft = False
                break
            j += 1

        if restart_at is not None and only_soft and j > i + 1:
            # Skip the preview opener; keep intervening soft blocks + real opener
            i += 1
            continue

        out.append(b)
        i += 1

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
            b = dict(block)
            b["text"] = _norm_text(b["text"])
            out.append(b)
            continue

        if block["type"] == "footnote":
            flush_para()
            flush_footnote()
            footnote_buf = dict(block)
            footnote_buf["text"] = _norm_text(footnote_buf["text"])
            continue

        if footnote_buf is not None:
            footnote_buf["text"] = _norm_text(
                f"{footnote_buf['text']} {block['text']}"
            )
            continue

        if para_buf is None:
            para_buf = dict(block)
            para_buf["text"] = _norm_text(para_buf["text"])
            continue

        same_page = para_buf["page"] == block["page"]
        if same_page or _paragraph_incomplete(para_buf["text"]):
            para_buf["text"] = _norm_text(f"{para_buf['text']} {block['text']}")
        else:
            flush_para()
            para_buf = dict(block)
            para_buf["text"] = _norm_text(para_buf["text"])

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
            title = _norm_text(b["text"])
            b["text"] = title
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
        text = _norm_text(block["text"])
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
        result = classify_line(
            line, body_size=body_size, level_map=level_map, noise_lines=noise_lines
        )
        if result:
            classified.extend(result)

    wrapped = merge_wrapped_headings(classified)
    continued = absorb_heading_continuations(wrapped)
    demoted = demote_implausible_headings(continued)
    part_titles = _part_title_set(noise_lines)
    pruned = drop_redundant_display_titles(demoted, part_titles=part_titles)
    releveled = relevel_numbered_headings(pruned, part_titles=part_titles)
    stripped = strip_preview_indexes(releveled)
    deduped = dedupe_preview_section_openers(stripped)
    no_toc = strip_toc_body(deduped)
    merged = merge_blocks(no_toc)
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
