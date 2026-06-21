# -*- coding: utf-8 -*-
"""Извлечение текста одной страницы PDF (PyMuPDF) с bbox, two-columns, сноски."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional

import fitz

from .blocks import is_junk, join_lines, merge_lines_to_paragraphs
from .colontitles import (
    ColontitleRegistry,
    is_page_number_block,
)
from .footnotes import extract_page_footnotes
from .roles import assign_heading_role

_HYPH_FIX = re.compile(r"(\w)[\-\u2010\u2013\u2014]\n(?=\w)")


def _block_infos_from_page(page: fitz.Page) -> List[Dict[str, Any]]:
    d = page.get_text("dict")
    raw_blocks = [b for b in d.get("blocks", []) if b.get("type", 0) == 0]
    block_infos: List[Dict[str, Any]] = []
    for b in raw_blocks:
        lines = b.get("lines", [])
        total_chars = 0
        wsum = 0.0
        for ln in lines:
            for sp in ln.get("spans", []):
                s = sp.get("size", 0)
                t = sp.get("text", "") or ""
                n = len(t)
                if n and s:
                    wsum += s * n
                    total_chars += n
        if total_chars == 0:
            continue
        block_infos.append({
            "raw": b,
            "wsize": wsum / total_chars,
            "chars": total_chars,
            "line_count": len(lines),
        })
    return block_infos


def _cluster_spans_to_lines(spans: List[Dict[str, Any]], *, y_tol: float = 4.0) -> List[Dict[str, Any]]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s["y0"], s["x0"]))
    lines: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []
    cur_y: Optional[float] = None
    for sp in spans:
        y0 = sp["y0"]
        if cur and cur_y is not None and abs(y0 - cur_y) > y_tol:
            lines.append(_merge_span_group(cur))
            cur = [sp]
            cur_y = y0
        else:
            cur.append(sp)
            cur_y = y0 if cur_y is None else (cur_y + y0) / 2
    if cur:
        lines.append(_merge_span_group(cur))
    return lines


def _merge_span_group(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    spans = sorted(spans, key=lambda s: s["x0"])
    text = ""
    for sp in spans:
        t = sp["text"]
        text = t if not text else join_lines(text, t)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    bboxes = [s["bbox"] for s in spans]
    from .bbox import union_many

    bbox = union_many(bboxes) or [0, 0, 0, 0]
    wavg = sum(s["wsize"] * max(len(s["text"]), 1) for s in spans) / max(
        sum(max(len(s["text"]), 1) for s in spans), 1
    )
    return {"text": text, "bbox": bbox, "wsize": round(wavg, 2)}


def _lines_from_page_spans(page: fitz.Page, *, two_columns: bool) -> List[Dict[str, Any]]:
    """Извлечение по span-ам — корректно для двухколоночных PDF."""
    page_width = page.rect.width
    center = page_width / 2
    d = page.get_text("dict")
    left_spans: List[Dict[str, Any]] = []
    right_spans: List[Dict[str, Any]] = []
    all_spans: List[Dict[str, Any]] = []

    for block in d.get("blocks") or []:
        if block.get("type", 0) != 0:
            continue
        for ln in block.get("lines") or []:
            for sp in ln.get("spans") or []:
                t = sp.get("text") or ""
                if not t.strip():
                    continue
                bbox = list(sp.get("bbox") or [0, 0, 0, 0])
                cx = (bbox[0] + bbox[2]) / 2
                entry = {
                    "text": t,
                    "bbox": bbox,
                    "wsize": float(sp.get("size") or 0),
                    "x0": bbox[0],
                    "y0": bbox[1],
                    "span": sp,
                    "raw_block": block,
                }
                all_spans.append(entry)
                if two_columns:
                    (left_spans if cx < center else right_spans).append(entry)
                else:
                    all_spans[-1] = entry

    if two_columns:
        left_lines = _cluster_spans_to_lines(left_spans)
        right_lines = _cluster_spans_to_lines(right_spans)
        return left_lines + right_lines

    return _cluster_spans_to_lines(all_spans)


def _apply_fn_markers_to_lines(
    lines: List[Dict[str, Any]],
    fn_id_map: Dict[str, int],
) -> List[Dict[str, Any]]:
    if not fn_id_map:
        return lines
    from .footnotes import is_superscript_digit

    out: List[Dict[str, Any]] = []
    for ln in lines:
        sp = ln.get("span")
        if sp:
            marker = (sp.get("text") or "").strip()
            med = ln.get("wsize") or 0
            if is_superscript_digit(sp, med) and marker in fn_id_map:
                ln = dict(ln)
                ln["text"] = f"{{{{fn:{fn_id_map[marker]}}}}}"
        text = ln.get("text") or ""
        text = _HYPH_FIX.sub(r"\1", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        if text:
            ln = dict(ln)
            ln["text"] = text
            out.append(ln)
    return out


def _lines_from_block_infos(
    block_infos: List[Dict[str, Any]],
    fn_body_indices: set[int],
    fn_id_map: Dict[str, int],
    *,
    poetry: bool,
) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for idx, bi in enumerate(block_infos):
        if idx in fn_body_indices:
            continue
        raw = bi["raw"]
        for ln in raw.get("lines") or []:
            spans = ln.get("spans") or []
            if not spans:
                continue
            chars = 0
            wsum = 0.0
            parts: List[str] = []
            block_med_sizes: List[float] = []
            for sp in spans:
                t = sp.get("text", "") or ""
                s = float(sp.get("size") or 0)
                n = len(t)
                marker = t.strip()
                if fn_id_map:
                    from .footnotes import is_superscript_digit, block_median_size

                    med = block_median_size(raw)
                    if is_superscript_digit(sp, med) and marker in fn_id_map:
                        parts.append(f"{{{{fn:{fn_id_map[marker]}}}}}")
                    else:
                        parts.append(t)
                else:
                    parts.append(t)
                if n and s:
                    wsum += s * n
                    chars += n
                    block_med_sizes.append(s)
            if chars == 0:
                continue
            text = "".join(parts)
            if not poetry:
                text = _HYPH_FIX.sub(r"\1", text)
                text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
            text = re.sub(r"[ \t]{2,}", " ", text).strip()
            if not text or is_junk(text):
                continue
            line_bbox = list(ln.get("bbox") or raw.get("bbox", [0, 0, 0, 0]))
            lines.append({
                "text": text,
                "bbox": line_bbox,
                "wsize": round(wsum / chars, 2),
            })
    return lines


def _sort_reading_order(
    lines: List[Dict[str, Any]],
    page_width: float,
    *,
    two_columns: bool,
) -> List[Dict[str, Any]]:
    if not lines:
        return lines
    if two_columns:
        center = page_width / 2
        left = [ln for ln in lines if (ln["bbox"][0] + ln["bbox"][2]) / 2 < center]
        right = [ln for ln in lines if (ln["bbox"][0] + ln["bbox"][2]) / 2 >= center]
        left.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
        right.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
        return left + right
    lines.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
    return lines


def extract_page(
    doc: fitz.Document,
    page_index: int,
    *,
    two_columns: bool = False,
    poetry: bool = False,
    fn_counter: Optional[Iterator[int]] = None,
    colontitles: Optional[ColontitleRegistry] = None,
) -> Dict[str, Any]:
    """page_index — 0-based."""
    page = doc[page_index]
    page_num = page_index + 1
    rect = page.rect
    page_height = rect.height
    page_width = rect.width

    block_infos = _block_infos_from_page(page)
    if not block_infos:
        return {
            "version": 2,
            "page": page_num,
            "width": round(rect.width, 2),
            "height": round(rect.height, 2),
            "engine": "pymupdf",
            "status": "extracted",
            "blocks": [],
            "footnotes": [],
        }

    all_wsizes = sorted(bi["wsize"] for bi in block_infos)
    med_size = all_wsizes[len(all_wsizes) // 2]

    fn_id_map: Dict[str, int] = {}
    page_footnotes: List[Dict[str, Any]] = []
    fn_body_indices: set[int] = set()
    if fn_counter is not None:
        fn_id_map, page_footnotes, fn_body_indices = extract_page_footnotes(
            block_infos, page_height, med_size, fn_counter
        )

    # Span-based extract: PDF часто кладёт каждое слово в отдельный «line» с чуть
    # разным y (justify); block-based + sort по y переворачивает порядок слов.
    lines = _lines_from_page_spans(page, two_columns=two_columns)
    if not two_columns:
        lines = _sort_reading_order(lines, page_width, two_columns=False)

    lines = _apply_fn_markers_to_lines(lines, fn_id_map)

    ct = colontitles or ColontitleRegistry()
    filtered_lines: List[Dict[str, Any]] = []
    for ln in lines:
        text = ln["text"]
        bbox = ln["bbox"]
        if is_page_number_block(text, bbox, page_height):
            continue
        if ct.is_colontitle(text, bbox, page_height):
            ct.observe_margin_text(text)
            continue
        if is_junk(text):
            continue
        cleaned = ct.strip_line_prefix(text)
        if not cleaned or ct._text_is_colontitle(cleaned):
            continue
        ln = dict(ln)
        ln["text"] = cleaned
        filtered_lines.append(ln)
    ct.observe_page()

    if two_columns:
        center = page_width / 2
        left = [ln for ln in filtered_lines if (ln["bbox"][0] + ln["bbox"][2]) / 2 < center]
        right = [ln for ln in filtered_lines if (ln["bbox"][0] + ln["bbox"][2]) / 2 >= center]
        left.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
        right.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
        paragraphs = merge_lines_to_paragraphs(
            left, page=page_num, page_height=page_height, poetry=poetry
        )
        paragraphs.extend(
            merge_lines_to_paragraphs(
                right, page=page_num, page_height=page_height, poetry=poetry
            )
        )
    else:
        filtered_lines = _sort_reading_order(filtered_lines, page_width, two_columns=False)
        paragraphs = merge_lines_to_paragraphs(
            filtered_lines,
            page=page_num,
            page_height=page_height,
            poetry=poetry,
        )

    if paragraphs:
        sizes = sorted(p.get("wsize") or 0 for p in paragraphs)
        med = sizes[len(sizes) // 2]
        thr = med * 1.5 + 1.0
    else:
        med = med_size
        thr = med_size * 1.5 + 1.0

    blocks: List[Dict[str, Any]] = []
    for i, para in enumerate(paragraphs):
        role = assign_heading_role(
            para,
            page_height=page_height,
            page_width=page_width,
            med_wsize=med,
            heading_thr=thr,
        )
        blocks.append({
            "id": f"p{page_num:03d}_b{i:02d}",
            "role": role,
            "text": para["text"],
            "bbox": para.get("bbox") or [0, 0, 0, 0],
            "wsize": para.get("wsize") or 0,
            "line_count": para.get("line_count") or 1,
        })

    return {
        "version": 2,
        "page": page_num,
        "width": round(rect.width, 2),
        "height": round(rect.height, 2),
        "engine": "pymupdf",
        "two_columns": two_columns,
        "status": "extracted",
        "blocks": blocks,
        "footnotes": page_footnotes,
    }


def open_pdf(pdf_path: str) -> fitz.Document:
    return fitz.open(pdf_path)
