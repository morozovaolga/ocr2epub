# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .align import (
    AlignedUnit,
    BlockRef,
    align_paragraphs_to_blocks,
    blocks_from_json,
    load_paragraphs,
    pick_status,
    similarity,
    strip_markup,
    word_diff,
)
from .pdf_spans import merge_pdf_spans, normalize_text, _is_valid_bbox


def split_our_text_by_alignment(our_text: str, ref_chunks: Sequence[str]) -> List[str]:
    """Разбить наш абзац на части по числу страниц/блоков (word-alignment с эталоном)."""
    if not ref_chunks:
        return [our_text]
    if len(ref_chunks) == 1:
        return [our_text]

    word_spans = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", our_text or "")]
    if not word_spans:
        return [""] * len(ref_chunks)

    ow = [w for w, _, _ in word_spans]
    ref_words: List[str] = []
    chunk_bounds: List[tuple[int, int]] = []
    for chunk in ref_chunks:
        start = len(ref_words)
        ref_words.extend(chunk.split())
        chunk_bounds.append((start, len(ref_words)))

    if not ref_words:
        weighted = split_text_by_weights(our_text, [1] * len(ref_chunks))
        parts = [p["our_text"] for p in weighted]
        if parts:
            head = "".join(parts[:-1])
            parts[-1] = our_text[len(head) :]
        return parts

    chunk_for_our: List[int] = [0] * len(ow)

    def chunk_at_ref_j(j: int) -> int:
        j = max(0, min(j, len(ref_words) - 1))
        for ci, (a, b) in enumerate(chunk_bounds):
            if a <= j < b:
                return ci
        return len(chunk_bounds) - 1

    sm = difflib.SequenceMatcher(a=ow, b=ref_words)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for oi in range(i1, i2):
                chunk_for_our[oi] = chunk_at_ref_j(j1 + (oi - i1))
        elif tag == "replace":
            for oi in range(i1, i2):
                j = j1 + (oi - i1)
                chunk_for_our[oi] = chunk_at_ref_j(j if j < len(ref_words) else len(ref_words) - 1)
        elif tag == "insert":
            prev = chunk_for_our[i1 - 1] if i1 > 0 else 0
            for oi in range(i1, i2):
                chunk_for_our[oi] = prev

    buckets: List[List[int]] = [[] for _ in ref_chunks]
    for oi, ci in enumerate(chunk_for_our):
        buckets[ci].append(oi)

    parts: List[str] = []
    for bi, word_ids in enumerate(buckets):
        if not word_ids:
            parts.append("")
            continue
        start = word_spans[word_ids[0]][1]
        end = word_spans[word_ids[-1]][2]
        parts.append(our_text[start:end])

    if parts:
        head_len = sum(len(p) for p in parts[:-1])
        # хвост абзаца целиком на последней странице — без потери пробелов/знаков
        parts[-1] = our_text[head_len:]

    # Word-alignment часто ломается, когда наш текст модернизирован, а блоки PDF — старые:
    # часть страниц получает 0 слов, другая — почти весь абзац.
    if len(parts) > 1 and (
        any(not (p or "").strip() for p in parts)
        or sum(len(p) for p in parts) < max(1, int(len(our_text or "") * 0.85))
    ):
        return _split_our_text_proportional(our_text, ref_chunks)
    return parts


def _split_our_text_proportional(our_text: str, ref_chunks: Sequence[str]) -> List[str]:
    """Пропорциональный рез по длине эталонных блоков (fallback)."""
    weights = [max(1, len(c.split())) for c in ref_chunks]
    weighted = split_text_by_weights(our_text, weights)
    parts = [p["our_text"] for p in weighted]
    if parts:
        head = sum(len(p) for p in parts[:-1])
        parts[-1] = our_text[head:]
    return parts


def split_text_by_weights(text: str, weights: List[int]) -> List[Dict[str, Any]]:
    """Разбить текст на части пропорционально весам (длины PDF по страницам)."""
    if not text:
        return []
    if not weights:
        return [{"page": 1, "our_text": text, "char_start": 0, "char_end": len(text)}]
    total = sum(weights) or 1
    slices: List[Dict[str, Any]] = []
    offset = 0
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            end = len(text)
        else:
            end = offset + max(1, round(len(text) * w / total))
        slices.append({
            "char_start": offset,
            "char_end": end,
            "our_text": text[offset:end],
        })
        offset = end
    return slices


def build_page_slices(
    unit: AlignedUnit,
    pdf_path: Path,
    block_by_id: Dict[int, BlockRef],
    *,
    pad: float = 2.0,
    review_threshold: float = 0.88,
) -> List[Dict[str, Any]]:
    if not unit.block_ids:
        t = unit.our_text
        return [{
            "page": 1,
            "our_text": t,
            "char_start": 0,
            "char_end": len(t),
            "pdf_text": "",
            "slice_key": f"{unit.paragraph_index}:1",
            "similarity": 0.0,
            "status": "no_pdf_text",
        }]

    unique_pages: List[int] = []
    for bid in unit.block_ids:
        page = block_by_id[bid].page
        if page not in unique_pages:
            unique_pages.append(page)

    page_ref_chunks: List[str] = []
    pdf_by_page: Dict[int, str] = {}
    for page in unique_pages:
        texts = [
            block_by_id[bid].text
            for bid in unit.block_ids
            if block_by_id[bid].page == page
        ]
        block_joined = normalize_text(" ".join(texts))
        page_ref_chunks.append(block_joined)
        spans = [(p, b) for p, b in zip(unit.pages, unit.bboxes) if p == page]
        if spans and any(_is_valid_bbox(b) for _, b in spans):
            pdf_by_page[page] = merge_pdf_spans(pdf_path, spans, pad=pad) if spans else ""
        else:
            # bbox в structured_rules часто [0,0,0,0] — не тянуть весь текстовый слой страницы
            pdf_by_page[page] = block_joined

    parts = split_our_text_by_alignment(unit.our_text, page_ref_chunks)

    out: List[Dict[str, Any]] = []
    offset = 0
    for page, part in zip(unique_pages, parts):
        pdf_part = pdf_by_page.get(page, "")
        sim = similarity(part, pdf_part) if pdf_part else 0.0
        diffs = word_diff(part, pdf_part) if pdf_part else []
        if not pdf_part:
            sl_status = "no_pdf_text"
        else:
            sl_status = pick_status(sim, diffs, review_threshold=review_threshold)
        end = offset + len(part)
        out.append({
            "page": page,
            "our_text": part,
            "char_start": offset,
            "char_end": end,
            "pdf_text": pdf_part,
            "slice_key": f"{unit.paragraph_index}:{page}",
            "similarity": round(sim, 4),
            "status": sl_status,
            "diff": diffs,
        })
        offset = end
    return out


def flatten_queue_to_pages(
    paragraph_items: List[Dict[str, Any]],
    block_by_id: Dict[int, BlockRef],
) -> List[Dict[str, Any]]:
    """Одна запись очереди = одна страница PDF (наш текст + bbox только этой страницы)."""
    flat: List[Dict[str, Any]] = []
    for item in paragraph_items:
        slices = item.get("page_slices") or []
        pi = int(item["paragraph_index"])
        if not slices:
            page = int((item.get("pages") or [1])[0])
            pages_bboxes = list(zip(item.get("pages") or [page], item.get("bboxes") or []))
            flat.append({
                "paragraph_index": pi,
                "page": page,
                "slice_index": 0,
                "slice_count": 1,
                "slice_key": f"{pi}:{page}",
                "our_text": item.get("our_text") or "",
                "pdf_text": item.get("pdf_text") or "",
                "block_ids": list(item.get("block_ids") or []),
                "pages": [p for p, _ in pages_bboxes] or [page],
                "bboxes": [b for _, b in pages_bboxes],
                "similarity": item.get("similarity", 0),
                "status": item.get("status", "review"),
                "diff": item.get("diff") or [],
            })
            continue
        for i, sl in enumerate(slices):
            page = int(sl["page"])
            pages_bboxes = [(p, b) for p, b in zip(item["pages"], item["bboxes"]) if int(p) == page]
            block_ids = [
                bid for bid in item.get("block_ids") or []
                if block_by_id.get(bid) and block_by_id[bid].page == page
            ]
            flat.append({
                "paragraph_index": pi,
                "page": page,
                "slice_index": i,
                "slice_count": len(slices),
                "slice_key": sl.get("slice_key") or f"{pi}:{page}",
                "our_text": sl.get("our_text") or "",
                "pdf_text": sl.get("pdf_text") or "",
                "block_ids": block_ids,
                "pages": [p for p, _ in pages_bboxes] or [page],
                "bboxes": [b for _, b in pages_bboxes],
                "similarity": sl.get("similarity", item.get("similarity")),
                "status": sl.get("status", item.get("status")),
                "diff": sl.get("diff") or [],
            })
    return flat


def paragraph_groups(items: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for item in items:
        pi = int(item["paragraph_index"])
        groups.setdefault(pi, []).append(item)
    for parts in groups.values():
        parts.sort(key=lambda x: (int(x.get("slice_index", 0)), int(x.get("page", 0))))
    return groups


STRUCTURED_CANDIDATES = (
    "book_structured.json",
    "final_structured.json",
    "structured_rules.json",
    "structured.json",
)

TEXT_CANDIDATES = (
    "final_corrected.txt",
    "final_better.txt",
    "final_llm.txt",
    "final_clean.txt",
    "final.txt",
)


def _our_text_for_block(b: Dict[str, Any]) -> str:
    for key in ("text_corrected", "text_modern", "text"):
        t = strip_markup(str(b.get(key) or ""))
        if t:
            return t
    return ""


def is_v3_workdir(workdir: Path, structured_data: Dict[str, Any]) -> bool:
    """Очередь по блокам bpp v3 (id + bbox), не page_first."""
    book_path = workdir / "book.json"
    if book_path.is_file():
        try:
            book = json.loads(book_path.read_text(encoding="utf-8"))
            if int(book.get("pipeline_version") or 0) >= 3:
                return True
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    if structured_data.get("pipeline") == "book_page_pipeline":
        blocks = structured_data.get("blocks") or []
        if blocks and blocks[0].get("id"):
            return True
    blocks = structured_data.get("blocks") or []
    return bool(
        blocks
        and blocks[0].get("id")
        and _is_valid_bbox(blocks[0].get("bbox"))
    )


def build_block_v3_items(
    blocks_raw: Sequence[Dict[str, Any]],
    pdf_path: Path,
    *,
    review_threshold: float = 0.88,
    bbox_pad: float = 2.0,
) -> List[Dict[str, Any]]:
    """Одна запись очереди = один блок book_structured (slice_key = block id)."""
    from .pdf_spans import extract_bbox_text

    items: List[Dict[str, Any]] = []
    for idx, b in enumerate(blocks_raw):
        ours = _our_text_for_block(b)
        if not ours:
            continue
        page = int(b.get("page") or 1)
        bbox = list(b.get("bbox") or [0, 0, 0, 0])
        block_id = str(b.get("id") or f"p{page:03d}_b{idx:02d}")
        role = (b.get("role") or "paragraph").lower()

        if _is_valid_bbox(bbox):
            pdf_part = normalize_text(extract_bbox_text(pdf_path, page, bbox, pad=bbox_pad))
        else:
            pdf_part = normalize_text(strip_markup(b.get("text") or b.get("text_raw") or ""))

        sim = similarity(ours, pdf_part) if pdf_part else 0.0
        diffs = word_diff(ours, pdf_part) if pdf_part else []
        if not pdf_part:
            status = "no_pdf_text"
        else:
            status = pick_status(sim, diffs, review_threshold=review_threshold)

        items.append({
            "paragraph_index": len(items),
            "block_id": block_id,
            "page": page,
            "slice_index": 0,
            "slice_count": 1,
            "slice_key": block_id,
            "role": role,
            "our_text": ours,
            "pdf_text": pdf_part,
            "block_ids": [idx],
            "pages": [page],
            "bboxes": [bbox],
            "similarity": round(sim, 4),
            "status": status,
            "diff": diffs,
            "confidence": b.get("confidence") or "",
            "block_status": b.get("status") or "",
        })
    return items


def build_queue_v3(
    pdf_path: Path,
    structured_path: Path,
    text_path: Path,
    *,
    review_threshold: float = 0.88,
    bbox_pad: float = 2.0,
    target_orthography: str = "modern",
) -> Dict[str, Any]:
    data = json.loads(structured_path.read_text(encoding="utf-8"))
    blocks_raw = list(data.get("blocks") or [])
    if not blocks_raw:
        raise ValueError(f"Нет блоков в {structured_path}")

    items = build_block_v3_items(
        blocks_raw,
        pdf_path,
        review_threshold=review_threshold,
        bbox_pad=bbox_pad,
    )
    if not items:
        raise ValueError(f"Нет блоков с текстом в {structured_path}")

    paragraphs = load_paragraphs(str(text_path)) if text_path.is_file() else []
    para_mismatch = len(paragraphs) != len(items) if paragraphs else False

    blocks_with_bbox = sum(1 for it in items if _is_valid_bbox((it.get("bboxes") or [[]])[0]))
    review_count = sum(1 for x in items if x["status"] == "review")
    ok_count = sum(1 for x in items if x["status"] == "ok")
    no_pdf = sum(1 for x in items if x["status"] == "no_pdf_text")
    empty_our = sum(1 for x in items if not (x.get("our_text") or "").strip())

    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pdf": str(pdf_path.resolve()),
            "structured": str(structured_path.resolve()),
            "text": str(text_path.resolve()) if text_path.is_file() else "",
            "target_orthography": target_orthography,
            "review_threshold": review_threshold,
            "paragraphs_total": len(items),
            "blocks_total": len(items),
            "pages_total": len({int(it["page"]) for it in items}),
            "pdf_pages_in_queue": len({int(it["page"]) for it in items}),
            "status_ok": ok_count,
            "status_review": review_count,
            "status_no_pdf_text": no_pdf,
            "status_review_slices": review_count,
            "empty_our_slices": empty_our,
            "queue_layout": "one_item_per_block_v3",
            "queue_build": "block_v3",
            "blocks_with_bbox": blocks_with_bbox,
            "paragraph_txt_mismatch": para_mismatch,
            "note": (
                "Очередь v3: один элемент = один блок book_structured с bbox-crop в UI. "
                "slice_key = block id (p013_b01)."
            ),
        },
        "items": items,
    }


def resolve_in_workdir(workdir: Path, names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        p = workdir / name
        if p.is_file():
            return p
    return None


def _paragraph_spans(paragraphs: Sequence[str]) -> Tuple[str, List[Tuple[int, int]]]:
    """Склеить абзацы как в final_better и вернуть диапазоны символов."""
    if not paragraphs:
        return "", []
    parts: List[str] = []
    ranges: List[Tuple[int, int]] = []
    offset = 0
    for i, para in enumerate(paragraphs):
        if i:
            offset += 2
        start = offset
        parts.append(para)
        offset += len(para)
        ranges.append((start, offset))
    return "\n\n".join(parts), ranges


def _pages_from_blocks(blocks: Sequence[BlockRef]) -> Tuple[List[int], List[str], Dict[int, List[BlockRef]]]:
    """Уникальные номера листов PDF по порядку + эталонный текст каждого листа."""
    by_page: Dict[int, List[BlockRef]] = {}
    page_order: List[int] = []
    for block in blocks:
        if block.page not in by_page:
            by_page[block.page] = []
            page_order.append(block.page)
        by_page[block.page].append(block)
    ref_chunks = [
        normalize_text(" ".join(b.text for b in by_page[page]))
        for page in page_order
    ]
    return page_order, ref_chunks, by_page


def build_page_first_items(
    blocks: Sequence[BlockRef],
    paragraphs: Sequence[str],
    pdf_path: Path,
    *,
    review_threshold: float = 0.88,
    bbox_pad: float = 2.0,
) -> Tuple[List[Dict[str, Any]], str, List[Tuple[int, int]]]:
    """
    Одна запись очереди = один лист PDF.
    Наш текст режется по всей книге пропорционально блокам structured на каждом листе.
    """
    full_our, para_ranges = _paragraph_spans(paragraphs)
    page_order, ref_chunks, by_page = _pages_from_blocks(blocks)
    parts = split_our_text_by_alignment(full_our, ref_chunks)

    items: List[Dict[str, Any]] = []
    offset = 0
    for page, part, ref in zip(page_order, parts, ref_chunks):
        page_blocks = by_page[page]
        spans = [(b.page, b.bbox) for b in page_blocks]
        if spans and any(_is_valid_bbox(b) for _, b in spans):
            pdf_part = merge_pdf_spans(pdf_path, spans, pad=bbox_pad)
        else:
            pdf_part = ref

        sim = similarity(part, pdf_part) if pdf_part else 0.0
        diffs = word_diff(part, pdf_part) if pdf_part else []
        if not pdf_part:
            status = "no_pdf_text"
        else:
            status = pick_status(sim, diffs, review_threshold=review_threshold)

        end = offset + len(part)
        items.append({
            "paragraph_index": int(page),
            "page": int(page),
            "slice_index": 0,
            "slice_count": 1,
            "slice_key": f"p:{page}",
            "our_text": part,
            "pdf_text": pdf_part,
            "char_start": offset,
            "char_end": end,
            "block_ids": [b.index for b in page_blocks],
            "pages": [int(page)],
            "bboxes": [b.bbox for b in page_blocks],
            "similarity": round(sim, 4),
            "status": status,
            "diff": diffs,
        })
        offset = end
    return items, full_our, para_ranges


def build_queue(
    pdf_path: Path,
    structured_path: Path,
    text_path: Path,
    *,
    review_threshold: float = 0.88,
    bbox_pad: float = 2.0,
    target_orthography: str = "modern",
) -> Dict[str, Any]:
    data = json.loads(structured_path.read_text(encoding="utf-8"))
    blocks = blocks_from_json(data)
    paragraphs = load_paragraphs(str(text_path))

    if not blocks:
        raise ValueError(f"Нет блоков с текстом в {structured_path}")
    if not paragraphs:
        raise ValueError(f"Нет абзацев в {text_path}")

    blocks_with_bbox = sum(1 for b in blocks if _is_valid_bbox(b.bbox))
    items, full_our, para_ranges = build_page_first_items(
        blocks,
        paragraphs,
        pdf_path,
        review_threshold=review_threshold,
        bbox_pad=bbox_pad,
    )

    review_count = sum(1 for x in items if x["status"] == "review")
    ok_count = sum(1 for x in items if x["status"] == "ok")
    no_pdf = sum(1 for x in items if x["status"] == "no_pdf_text")
    empty_our = sum(1 for x in items if not (x.get("our_text") or "").strip())
    para_count = len(paragraphs)

    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pdf": str(pdf_path.resolve()),
            "structured": str(structured_path.resolve()),
            "text": str(text_path.resolve()),
            "target_orthography": target_orthography,
            "review_threshold": review_threshold,
            "paragraphs_total": para_count,
            "pages_total": len(items),
            "pdf_pages_in_queue": len(items),
            "status_ok": ok_count,
            "status_review": review_count,
            "status_no_pdf_text": no_pdf,
            "status_review_slices": review_count,
            "empty_our_slices": empty_our,
            "queue_layout": "one_item_per_pdf_page_v2",
            "queue_build": "page_first",
            "blocks_with_bbox": blocks_with_bbox,
            "blocks_total": len(blocks),
            "full_our_length": len(full_our),
            "paragraph_ranges": para_ranges,
            "note": (
                "Очередь по листам PDF: один элемент = один лист, наш текст — срез final_better "
                "на этот лист (не абзац, разрезанный эвристикой). "
                + (
                    "bbox в structured нулевые — слева целая страница, pdf_text из блоков structured."
                    if blocks_with_bbox == 0
                    else ""
                )
            ),
        },
        "items": items,
    }


def run_from_workdir(
    pdf_path: Path | None,
    workdir: Path,
    out_path: Path,
    *,
    structured: Optional[Path] = None,
    text: Optional[Path] = None,
    review_threshold: float = 0.88,
    target_orthography: str = "modern",
) -> Dict[str, Any]:
    if pdf_path is None:
        from .workdir import resolve_pdf as resolve_workdir_pdf

        pdf_path = resolve_workdir_pdf(workdir)
    structured_path = structured or resolve_in_workdir(workdir, STRUCTURED_CANDIDATES)
    text_path = text or resolve_in_workdir(workdir, TEXT_CANDIDATES)
    if not structured_path:
        raise FileNotFoundError(
            f"Не найден structured JSON в {workdir} "
            f"(ожидали: {', '.join(STRUCTURED_CANDIDATES)})"
        )
    if not text_path:
        raise FileNotFoundError(
            f"Не найден TXT в {workdir} (ожидали: {', '.join(TEXT_CANDIDATES)})"
        )

    structured_data = json.loads(structured_path.read_text(encoding="utf-8"))
    if is_v3_workdir(workdir, structured_data):
        queue = build_queue_v3(
            pdf_path,
            structured_path,
            text_path,
            review_threshold=review_threshold,
            target_orthography=target_orthography,
        )
    else:
        queue = build_queue(
            pdf_path,
            structured_path,
            text_path,
            review_threshold=review_threshold,
            target_orthography=target_orthography,
        )
    try:
        from .page_snapshots import export_page_snapshots

        export_page_snapshots(pdf_path, queue["items"], workdir / "corrector_pages")
        queue["meta"]["page_snapshots_dir"] = str((workdir / "corrector_pages").resolve())
    except OSError:
        pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return queue


def merge_paragraph_from_decisions(
    item: Dict[str, Any],
    decisions: Dict[str, Any],
) -> Optional[str]:
    """Собрать полный абзац из правок по страницам (ключи «pi:page») или legacy «pi»."""
    pi = int(item["paragraph_index"])
    idx = str(pi)
    dec_para = decisions.get(idx)
    slices = item.get("page_slices") or []

    if slices:
        slice_keys = [str(sl.get("slice_key") or f"{pi}:{sl['page']}") for sl in slices]
        if any(k in decisions for k in slice_keys):
            parts: List[str] = []
            for sl, sk in zip(slices, slice_keys):
                sd = decisions.get(sk)
                if sd and sd.get("edited_text") is not None:
                    parts.append(sd["edited_text"])
                else:
                    parts.append(sl.get("our_text") or "")
            return "".join(parts)

    if not dec_para:
        return None
    action = dec_para.get("action")
    if action == "use_pdf":
        return item.get("pdf_text") or item.get("our_text") or ""
    if action in ("edit", "accept_llm") and dec_para.get("edited_text") is not None:
        edited = dec_para["edited_text"]
        if dec_para.get("slice_texts") and slices:
            return "".join(dec_para["slice_texts"])
        return edited
    return None


def apply_page_first_decisions(
    items: List[Dict[str, Any]],
    decisions: Dict[str, Any],
    paragraphs: List[str],
) -> None:
    """Применить правки по листам PDF (slice_key p:N, char_start/char_end)."""
    full_our, _ = _paragraph_spans(paragraphs)
    new_full = full_our
    for item in sorted(items, key=lambda x: -int(x.get("char_start") or 0)):
        key = str(item.get("slice_key") or f"p:{item.get('page')}")
        dec = decisions.get(key)
        if not dec or dec.get("edited_text") is None:
            continue
        start = int(item.get("char_start") or 0)
        end = int(item.get("char_end") or start)
        new_full = new_full[:start] + str(dec["edited_text"]) + new_full[end:]

    new_parts = load_paragraphs_from_string(new_full)
    if new_parts:
        paragraphs[:] = new_parts


def load_paragraphs_from_string(raw: str) -> List[str]:
    parts = [strip_markup(p) for p in re.split(r"\n\s*\n", raw)]
    return [p for p in parts if p]


def apply_v3_block_decisions(
    items: List[Dict[str, Any]],
    decisions: Dict[str, Any],
    paragraphs: List[str],
) -> None:
    """Правки по slice_key (= block id) → paragraphs[paragraph_index]."""
    for item in items:
        pi = int(item.get("paragraph_index", -1))
        if pi < 0 or pi >= len(paragraphs):
            continue
        key = str(item.get("slice_key") or item.get("block_id") or "")
        dec = decisions.get(key)
        if not dec:
            continue
        action = dec.get("action")
        if action == "use_pdf":
            paragraphs[pi] = item.get("pdf_text") or item.get("our_text") or paragraphs[pi]
        elif action in ("edit", "accept_llm", "keep_ours") and dec.get("edited_text") is not None:
            paragraphs[pi] = dec["edited_text"]
        elif action == "skip":
            continue


def sync_v3_decisions_to_pages(
    workdir: Path,
    items: List[Dict[str, Any]],
    decisions: Dict[str, Any],
    paragraphs: List[str],
) -> int:
    """Записать text_corrected в pages/page_NNNN.json (bpp workdir)."""
    page_dir = workdir / "pages"
    if not page_dir.is_dir():
        return 0

    updates_by_page: Dict[int, Dict[str, str]] = {}
    for item in items:
        pi = int(item.get("paragraph_index", -1))
        block_id = str(item.get("block_id") or item.get("slice_key") or "")
        if not block_id:
            continue
        page = int(item.get("page") or 1)
        text: Optional[str] = None
        key = str(item.get("slice_key") or block_id)
        dec = decisions.get(key)
        if dec and dec.get("edited_text") is not None:
            text = str(dec["edited_text"])
        elif 0 <= pi < len(paragraphs):
            text = paragraphs[pi]
        if text is None:
            continue
        updates_by_page.setdefault(page, {})[block_id] = text

    pages_updated = 0
    for page, id_to_text in updates_by_page.items():
        path = page_dir / f"page_{page:04d}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        changed = False
        for block in data.get("blocks") or []:
            bid = str(block.get("id") or "")
            if bid not in id_to_text:
                continue
            block["text_corrected"] = id_to_text[bid]
            block["status"] = "human"
            block["confidence"] = "high"
            changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            pages_updated += 1
    return pages_updated


def apply_all_decisions(
    items: List[Dict[str, Any]],
    decisions: Dict[str, Any],
    paragraphs: List[str],
    *,
    queue_layout: str = "",
) -> None:
    """Применить решения к списку абзацев."""
    layout = queue_layout or (items[0].get("queue_layout") if items else "")
    if layout == "one_item_per_block_v3" or (items and items[0].get("block_id")):
        apply_v3_block_decisions(items, decisions, paragraphs)
        return

    if items and str(items[0].get("slice_key") or "").startswith("p:"):
        apply_page_first_decisions(items, decisions, paragraphs)
        return

    if items and items[0].get("page") is not None and not items[0].get("page_slices"):
        for pi, parts in paragraph_groups(items).items():
            if pi >= len(paragraphs):
                continue
            virtual = {
                "paragraph_index": pi,
                "our_text": "".join(p.get("our_text") or "" for p in parts),
                "pdf_text": "".join(p.get("pdf_text") or "" for p in parts),
                "page_slices": [
                    {
                        "page": p["page"],
                        "our_text": p.get("our_text") or "",
                        "slice_key": p.get("slice_key") or f"{pi}:{p['page']}",
                    }
                    for p in parts
                ],
            }
            merged = merge_paragraph_from_decisions(virtual, decisions)
            if merged is not None:
                paragraphs[pi] = merged
        return

    for item in items:
        pi = int(item["paragraph_index"])
        if pi >= len(paragraphs):
            continue
        merged = merge_paragraph_from_decisions(item, decisions)
        if merged is not None:
            paragraphs[pi] = merged
