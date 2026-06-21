# -*- coding: utf-8 -*-
"""Лог правок для анализа и дообучения моделей."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def resolved_text(item: Dict[str, Any], decision: Dict[str, Any]) -> str:
    action = decision.get("action")
    our_text = item.get("our_text") or ""
    pdf_text = item.get("pdf_text") or ""
    if action == "use_pdf":
        return pdf_text or our_text
    if action in ("edit", "accept_llm") and decision.get("edited_text") is not None:
        return decision["edited_text"]
    return our_text


def training_label(action: str, *, had_diff: bool) -> str:
    if action == "edit":
        return "human_edit"
    if action == "accept_llm":
        return "accept_llm"
    if action == "use_pdf":
        return "accept_pdf"
    if action == "keep_ours":
        return "keep_despite_pdf_diff" if had_diff else "keep_unchanged"
    if action == "skip":
        return "skipped"
    return action or "unknown"


def _format_diff_for_prompt(diffs: List[Dict[str, Any]], *, limit: int = 12) -> str:
    lines: List[str] = []
    for d in diffs[:limit]:
        op = d.get("op") or "?"
        ours = d.get("ours") or "—"
        pdf = d.get("pdf") or "—"
        lines.append(f"- [{op}] наш: {ours} | PDF: {pdf}")
    if len(diffs) > limit:
        lines.append(f"- … ещё {len(diffs) - limit} расхождений")
    return "\n".join(lines) if lines else "(нет)"


def build_sft_prompt(
    item: Dict[str, Any],
    *,
    target_orthography: str = "modern",
) -> Dict[str, str]:
    ortho_note = (
        "Сохраняй модернизированную орфографию; отличия PDF вроде ѣ/ъ — не всегда ошибка."
        if target_orthography == "modern"
        else "Сверяй буква в букву с PDF."
    )
    instruction = (
        "Исправь абзац русского текста, сверяясь с текстовым слоем PDF. "
        f"{ortho_note}"
    )
    diff_block = _format_diff_for_prompt(item.get("diff") or [])
    pages = ", ".join(str(p) for p in (item.get("pages") or [])) or "—"
    input_text = (
        f"Страницы PDF: {pages}\n"
        f"Similarity: {item.get('similarity', 0)}\n\n"
        f"Наш текст:\n{item.get('our_text') or ''}\n\n"
        f"Текст PDF:\n{item.get('pdf_text') or ''}\n\n"
        f"Расхождения:\n{diff_block}"
    )
    return {
        "instruction": instruction,
        "input": input_text,
    }


def build_training_record(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    *,
    queue_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = queue_meta or {}
    action = decision.get("action") or ""
    diffs = item.get("diff") or []
    corrected = resolved_text(item, decision)
    our_text = item.get("our_text") or ""
    changed = corrected != our_text
    sft = build_sft_prompt(
        item,
        target_orthography=str(meta.get("target_orthography") or "modern"),
    )
    sft["output"] = corrected

    at = decision.get("at") or datetime.now(timezone.utc).isoformat()
    pi = int(item.get("paragraph_index", -1))

    return {
        "at": at,
        "paragraph_index": pi,
        "action": action,
        "label": training_label(action, had_diff=bool(diffs)),
        "changed": changed,
        "our_text": our_text,
        "pdf_text": item.get("pdf_text") or "",
        "corrected_text": corrected,
        "diff": diffs,
        "similarity": item.get("similarity"),
        "status": item.get("status"),
        "pages": item.get("pages") or [],
        "block_ids": item.get("block_ids") or [],
        "target_orthography": meta.get("target_orthography"),
        "source": {
            "pdf": meta.get("pdf"),
            "text": meta.get("text"),
            "structured": meta.get("structured"),
            "queue": meta.get("queue"),
        },
        "sft": sft,
        "dpo": {
            "prompt": sft["instruction"] + "\n\n" + sft["input"],
            "chosen": corrected,
            "rejected": item.get("pdf_text") or our_text if action == "keep_ours" else our_text,
        },
    }


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def rebuild_training_latest(jsonl_path: Path, latest_path: Path) -> int:
    """Одна последняя запись на paragraph_index — удобно для SFT."""
    if not jsonl_path.is_file():
        latest_path.write_text("{}\n", encoding="utf-8")
        return 0

    by_para: Dict[int, Dict[str, Any]] = {}
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        by_para[int(rec["paragraph_index"])] = rec

    rows = [by_para[k] for k in sorted(by_para)]
    latest_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return len(rows)


def export_openai_messages(
    records: List[Dict[str, Any]],
    *,
    include_unchanged: bool = False,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in records:
        if not include_unchanged and not rec.get("changed"):
            continue
        sft = rec.get("sft") or {}
        out.append({
            "messages": [
                {"role": "system", "content": sft.get("instruction", "")},
                {"role": "user", "content": sft.get("input", "")},
                {"role": "assistant", "content": sft.get("output", "")},
            ],
            "metadata": {
                "paragraph_index": rec.get("paragraph_index"),
                "label": rec.get("label"),
                "action": rec.get("action"),
            },
        })
    return out
