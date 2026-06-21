# -*- coding: utf-8 -*-
"""LLM-коррекция по блокам: локальный Ollama find_apply, опционально облако."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .bbox import is_valid_bbox
from .page_store import load_manifest, load_page, save_page
from .paths import assets_dir, default_corrector_root
from .workdir import load_book, resolve_pdf, save_book, update_stage


def _ensure_corrector_path() -> None:
    root = default_corrector_root()
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _import_corrector():
    _ensure_corrector_path()
    try:
        from pdf_faithful_corrector.align import pick_status, similarity, word_diff
        from pdf_faithful_corrector.learned_rules import (
            format_rules_for_prompt,
            global_learned_path,
            load_merged_rules_jsonl,
            load_rules_jsonl,
        )
        from pdf_faithful_corrector.ollama_judge import ollama_status, suggest_correction
        from pdf_faithful_corrector.pdf_spans import extract_bbox_text, normalize_text
    except ImportError as e:
        raise ImportError(
            "Нужен pdf_faithful_corrector (pip install -e . из корня репозитория)"
        ) from e
    return {
        "similarity": similarity,
        "word_diff": word_diff,
        "pick_status": pick_status,
        "format_rules_for_prompt": format_rules_for_prompt,
        "load_rules_jsonl": load_rules_jsonl,
        "load_merged_rules_jsonl": load_merged_rules_jsonl,
        "global_learned_path": global_learned_path,
        "suggest_correction": suggest_correction,
        "extract_bbox_text": extract_bbox_text,
        "normalize_text": normalize_text,
        "ollama_status": ollama_status,
    }


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _learned_rules_path(out_dir: Path) -> Path:
    return out_dir / "rules_learned.jsonl"


def _log_path(out_dir: Path) -> Path:
    return out_dir / "logs" / "llm_trace.jsonl"


def _pdf_text_for_block(
    pdf_path: Path,
    block: Dict[str, Any],
    *,
    extract_bbox_text,
    normalize_text,
) -> str:
    page = int(block.get("page") or 1)
    bbox = block.get("bbox") or [0, 0, 0, 0]
    if is_valid_bbox(bbox):
        raw = extract_bbox_text(pdf_path, page, list(bbox))
    else:
        from pdf_faithful_corrector.pdf_spans import extract_page_text

        raw = extract_page_text(pdf_path, page)
    return normalize_text(raw)


def _try_cloud_correct(text: str) -> Tuple[Optional[str], Optional[str]]:
    """GigaChat для блоков после локальной LLM (если есть credentials)."""
    creds = os.environ.get("GIGACHAT_CREDENTIALS") or os.environ.get("GIGACHAT_API_KEY")
    if not creds:
        return None, "нет GIGACHAT_CREDENTIALS"
    assets = assets_dir()
    if assets.is_dir() and str(assets) not in sys.path:
        sys.path.insert(0, str(assets))
    try:
        from llm_correction import correct_gigachat
    except ImportError:
        return None, "llm_correction не найден"
    try:
        fixed, stats, _doubts = correct_gigachat(
            text,
            credentials=creds,
            chunk_size=max(len(text) + 500, 4000),
            cautious=True,
            old_russian=True,
            sleep=0.2,
        )
    except Exception as e:
        return None, str(e)
    if stats.get("error"):
        return None, str(stats.get("error"))
    return fixed, None


def correct_block(
    block: Dict[str, Any],
    *,
    pdf_path: Path,
    cx: Optional[Dict[str, Any]] = None,
    review_threshold: float = 0.88,
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_model: str = "qwen2.5:3b",
    ollama_timeout: float = 300,
    use_cloud: bool = False,
    rules_hint: Optional[str] = None,
    force: bool = False,
    review_only: bool = False,
    max_retries: int = 1,
) -> Dict[str, Any]:
    cx = cx or _import_corrector()
    ours = (block.get("text_modern") or block.get("text") or "").strip()
    nb = dict(block)
    if not ours:
        nb["text_corrected"] = ""
        nb["confidence"] = "empty"
        nb["status"] = "ok"
        return nb

    if (
        not force
        and nb.get("text_corrected")
        and nb.get("status") in ("llm", "ok", "human")
    ):
        return nb

    pdf_txt = _pdf_text_for_block(
        pdf_path,
        block,
        extract_bbox_text=cx["extract_bbox_text"],
        normalize_text=cx["normalize_text"],
    )
    sim_before = cx["similarity"](ours, pdf_txt) if pdf_txt else 0.0
    diffs = cx["word_diff"](ours, pdf_txt) if pdf_txt else []
    status_before = cx["pick_status"](sim_before, diffs, review_threshold=review_threshold)

    if review_only and status_before == "ok" and not force:
        nb["text_corrected"] = ours
        nb["confidence"] = "high"
        nb["status"] = "modern"
        nb["similarity_before"] = round(sim_before, 4)
        return nb

    item = {
        "our_text": ours,
        "pdf_text": pdf_txt,
        "pages": [int(block.get("page") or 1)],
        "diff": diffs,
    }

    try:
        result = None
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                result = cx["suggest_correction"](
                    item,
                    our_text=ours,
                    target_orthography="modern",
                    model=ollama_model,
                    base_url=ollama_url,
                    timeout=ollama_timeout,
                    rules_hint=rules_hint,
                    mode="find_apply",
                )
                break
            except Exception as e:
                last_err = e
                if attempt >= max_retries:
                    raise
        if result is None and last_err:
            raise last_err
    except Exception as e:
        nb["text_corrected"] = ours
        nb["confidence"] = "review"
        nb["status"] = "llm_error"
        nb["llm_error"] = str(e)
        return nb

    corrected = (result.get("suggested_text") or ours).strip()
    engine = "ollama"

    if use_cloud and status_before == "review":
        sim_after_local = cx["similarity"](corrected, pdf_txt) if pdf_txt else 1.0
        if sim_after_local < review_threshold:
            cloud_fixed, cloud_err = _try_cloud_correct(corrected)
            if cloud_fixed and cloud_fixed.strip() != corrected:
                corrected = cloud_fixed.strip()
                engine = "ollama+cloud"
            elif cloud_err:
                nb["cloud_skip"] = cloud_err

    sim_after = cx["similarity"](corrected, pdf_txt) if pdf_txt else 1.0
    conf = cx["pick_status"](sim_after, diffs, review_threshold=review_threshold)

    nb["text_corrected"] = corrected
    nb["similarity_before"] = round(sim_before, 4)
    nb["similarity_after"] = round(sim_after, 4)
    nb["confidence"] = "review" if conf == "review" else "high"
    nb["status"] = "llm" if corrected != ours else "modern"
    nb["llm_engine"] = engine
    nb["llm_model"] = ollama_model
    nb["llm_changed"] = corrected != ours
    nb["llm_edits_applied"] = result.get("edits_applied") or []
    nb["llm_edits_rejected"] = result.get("edits_rejected") or []
    if result.get("orthography_warning"):
        nb["llm_warning"] = result["orthography_warning"]
    return nb


def _rules_hint(out_dir: Path, cx: Dict[str, Any]) -> Optional[str]:
    local_path = _learned_rules_path(out_dir)
    global_path = cx["global_learned_path"]()
    rules = cx["load_merged_rules_jsonl"](global_path, local_path)
    return cx["format_rules_for_prompt"](rules) or None


def correct_workdir(
    out_dir: Path,
    *,
    page_from: int = 1,
    page_to: Optional[int] = None,
    review_only: bool = True,
    review_threshold: float = 0.88,
    force: bool = False,
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_model: str = "qwen2.5:3b",
    ollama_timeout: float = 300,
    use_cloud: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    cx = _import_corrector()
    st = cx["ollama_status"](ollama_url, ollama_model)
    if not st.get("ok"):
        raise RuntimeError(st.get("error") or "Ollama недоступен")
    if not st.get("model_available"):
        raise RuntimeError(
            f"Модель {ollama_model!r} не найдена в Ollama. "
            f"Доступны: {', '.join(st.get('models') or [])[:5]}"
        )

    pdf_path = resolve_pdf(out_dir)
    manifest = load_manifest(out_dir)
    page_dir = out_dir / (manifest.get("page_dir") or "pages")
    completed = sorted(int(p) for p in (manifest.get("completed_pages") or []))
    if not completed:
        completed = sorted(
            int(p.stem.split("_")[1]) for p in page_dir.glob("page_*.json")
        )
    if page_to:
        completed = [p for p in completed if page_from <= p <= page_to]
    else:
        completed = [p for p in completed if p >= page_from]

    hint = _rules_hint(out_dir, cx)
    log_path = _log_path(out_dir)
    learned_path = _learned_rules_path(out_dir)

    blocks_done = 0
    blocks_changed = 0
    blocks_review = 0
    blocks_skipped = 0

    blocks_errors = 0

    def _log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    for page in completed:
        data = load_page(page_dir, page)
        if not data:
            continue
        blocks_on_page = data.get("blocks") or []
        _log(f"Стр. {page}: {len(blocks_on_page)} блок(ов)")
        new_blocks: List[Dict[str, Any]] = []
        for bi, b in enumerate(blocks_on_page):
            b = dict(b)
            b.setdefault("page", page)
            bid = b.get("id") or f"p{page:03d}_b{bi:02d}"
            b = correct_block(
                b,
                pdf_path=pdf_path,
                cx=cx,
                review_threshold=review_threshold,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                ollama_timeout=ollama_timeout,
                use_cloud=use_cloud,
                rules_hint=hint,
                force=force,
                review_only=review_only,
            )
            tag = b.get("status") or "?"
            changed = "+" if b.get("llm_changed") else "="
            _log(f"  {bid} [{tag}] {changed}")
            if b.get("status") == "llm_error":
                blocks_errors += 1
            if review_only and b.get("status") == "modern" and not force:
                blocks_skipped += 1
            else:
                blocks_done += 1
            if b.get("llm_changed"):
                blocks_changed += 1
            if b.get("confidence") == "review":
                blocks_review += 1

            _append_jsonl(
                log_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "block_id": b.get("id"),
                    "page": page,
                    "engine": b.get("llm_engine"),
                    "changed": b.get("llm_changed"),
                    "confidence": b.get("confidence"),
                    "edits_applied": b.get("llm_edits_applied"),
                },
            )
            for ed in b.get("llm_edits_applied") or []:
                old = ed.get("old") or ed.get("from")
                new = ed.get("new") or ed.get("to")
                if old and new and old != new:
                    _append_jsonl(
                        learned_path,
                        {
                            "from": old,
                            "to": new,
                            "source": "llm_find_apply",
                            "block_id": b.get("id"),
                            "page": page,
                        },
                    )
            new_blocks.append(b)

        data["blocks"] = new_blocks
        for fn in data.get("footnotes") or []:
            if not fn.get("text_corrected"):
                fn["text_corrected"] = fn.get("text_modern") or fn.get("text") or ""
        data["llm_pass_at"] = datetime.now(timezone.utc).isoformat()
        save_page(page_dir, data)

    book = load_book(out_dir) or {}
    book.setdefault("stages", {})["correct"] = "done" if not review_only else "partial"
    save_book(out_dir, book)
    update_stage(out_dir, "review", "pending")

    return {
        "out_dir": str(out_dir.resolve()),
        "pages": len(completed),
        "blocks_processed": blocks_done,
        "blocks_skipped": blocks_skipped,
        "blocks_changed": blocks_changed,
        "blocks_review": blocks_review,
        "blocks_errors": blocks_errors,
        "ollama_model": ollama_model,
        "log": str(log_path),
    }
