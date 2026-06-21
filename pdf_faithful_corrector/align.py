# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .pdf_spans import normalize_for_match, normalize_text

_TAG_RE = re.compile(r"<[^>]+>")


def strip_markup(text: str) -> str:
    return normalize_text(_TAG_RE.sub("", text))


def similarity(a: str, b: str) -> float:
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(a=na, b=nb).ratio()


def load_paragraphs(text_path: str) -> List[str]:
    raw = open(text_path, encoding="utf-8", errors="replace").read()
    parts = [strip_markup(p) for p in re.split(r"\n\s*\n", raw)]
    return [p for p in parts if p]


@dataclass
class BlockRef:
    index: int
    page: int
    bbox: List[float]
    role: str
    text: str


def blocks_from_json(data: dict) -> List[BlockRef]:
    out: List[BlockRef] = []
    for i, b in enumerate(data.get("blocks") or []):
        txt = strip_markup(b.get("text") or "")
        if not txt:
            continue
        out.append(
            BlockRef(
                index=i,
                page=int(b.get("page") or 1),
                bbox=list(b.get("bbox") or [0, 0, 0, 0]),
                role=(b.get("role") or "paragraph").lower(),
                text=txt,
            )
        )
    return out


@dataclass
class AlignedUnit:
    paragraph_index: int
    our_text: str
    block_ids: List[int]
    pages: List[int]
    bboxes: List[List[float]]
    block_text_joined: str = ""


def align_paragraphs_to_blocks(
    paragraphs: Sequence[str],
    blocks: Sequence[BlockRef],
    *,
    max_blocks_per_para: int = 8,
) -> List[AlignedUnit]:
    """Greedy alignment: map each final_better paragraph to 1..N structured blocks."""
    aligned: List[AlignedUnit] = []
    bi = 0
    for pi, para in enumerate(paragraphs):
        if bi >= len(blocks):
            aligned.append(
                AlignedUnit(
                    paragraph_index=pi,
                    our_text=para,
                    block_ids=[],
                    pages=[],
                    bboxes=[],
                )
            )
            continue

        best_end = bi
        best_score = -1.0
        best_joined = blocks[bi].text
        acc = blocks[bi].text
        for end in range(bi, min(bi + max_blocks_per_para, len(blocks))):
            if end > bi:
                acc = normalize_text(acc + " " + blocks[end].text)
            sc = similarity(acc, para)
            if sc >= best_score:
                best_score = sc
                best_end = end
                best_joined = acc

        chunk = blocks[bi : best_end + 1]
        aligned.append(
            AlignedUnit(
                paragraph_index=pi,
                our_text=para,
                block_ids=[b.index for b in chunk],
                pages=[b.page for b in chunk],
                bboxes=[b.bbox for b in chunk],
                block_text_joined=best_joined,
            )
        )
        bi = best_end + 1
    return aligned


def word_diff(ours: str, pdf: str, *, context: int = 2) -> List[Dict[str, Any]]:
    """Token-level diff; returns only replace/insert/delete ops."""
    ow = ours.split()
    pw = pdf.split()
    sm = difflib.SequenceMatcher(a=ow, b=pw)
    items: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        items.append({
            "op": tag,
            "ours": " ".join(ow[i1:i2]),
            "pdf": " ".join(pw[j1:j2]),
            "ours_span": [i1, i2],
            "pdf_span": [j1, j2],
        })
    return items


def pick_status(sim: float, diffs: List[dict], *, review_threshold: float) -> str:
    if sim >= 0.98 and not diffs:
        return "ok"
    if sim < review_threshold or diffs:
        return "review"
    return "ok"
