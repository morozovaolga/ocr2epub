# -*- coding: utf-8 -*-
"""Предложения правок через локальный Ollama (судья для status=review)."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .edit_pipeline import ApplyResult, TextEdit, apply_edits, parse_edits_json
from .modernize import (
    has_old_orthography,
    modernize_for_prompt_reference,
    modernize_prompt_block,
    modernize_text,
    sanitize_llm_output,
    strip_noise as _strip_noise,
)
from .training_log import _format_diff_for_prompt

_DEFAULT_URL = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "qwen2.5:3b"
_TIMEOUT_S = 300
_MAX_OUR_CHARS = 3000
_MAX_PDF_CHARS = 2500
_OLLAMA_MODES = ("find_apply", "rewrite")


def _has_old_orthography(text: str) -> bool:
    return has_old_orthography(text)


def _modern_orthography_rules() -> str:
    return modernize_prompt_block()


def _http_json(
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    method: str = "GET",
    timeout: float = _TIMEOUT_S,
) -> Dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {body[:300]}") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower():
            raise RuntimeError(
                f"Ollama не ответил за {int(timeout)} с. "
                "Модель грузится или не хватает RAM — попробуйте меньшую модель "
                "(qwen2.5:3b) или перезапустите ollama serve."
            ) from e
        raise RuntimeError(
            f"Ollama недоступен ({url}). Запустите: ollama serve"
        ) from e
    return json.loads(raw or "{}")


def list_models(base_url: str = _DEFAULT_URL) -> List[str]:
    data = _http_json(f"{base_url.rstrip('/')}/api/tags", timeout=15)
    models = []
    for m in data.get("models") or []:
        name = m.get("name")
        if name:
            models.append(name)
    return models


def ollama_status(
    base_url: str = _DEFAULT_URL,
    model: str = _DEFAULT_MODEL,
) -> Dict[str, Any]:
    try:
        models = list_models(base_url)
        return {
            "ok": True,
            "base_url": base_url,
            "model": model,
            "model_available": any(
                model == m or m.startswith(model + ":") or model.startswith(m.split(":")[0])
                for m in models
            ),
            "models": models[:20],
        }
    except RuntimeError as e:
        return {"ok": False, "base_url": base_url, "model": model, "error": str(e)}


def _system_prompt_find(target_orthography: str) -> str:
    base = (
        "Ты ищешь ошибки OCR в русском тексте книги. "
        "НЕ переписывай абзац целиком — только выпиши точечные правки.\n\n"
    )
    if target_orthography == "faithful":
        return (
            base
            + "Сверяйся с PDF. Каждая правка: подстрока из НАШЕГО текста (old) → исправление (new).\n"
            "Верни ТОЛЬКО JSON без markdown:\n"
            '{"edits":[{"old":"…","new":"…","reason":"ocr|missing|punctuation"}]}\n'
            "Если правок нет: {\"edits\":[]}"
        )
    return (
        base
        + _modern_orthography_rules()
        + "\n\n"
        "Правила для edits:\n"
        "• old — дословный фрагмент из НАШЕГО текста (1–80 символов), который встречается ровно один раз.\n"
        "• new — исправление в современной орфографии; не копируй дореформенные буквы из PDF.\n"
        "• reason: ocr | missing | punctuation | grammar\n"
        "• Не меняй стиль, синонимы и удачные современные слова.\n"
        "• Не предлагай правки «наш → PDF», если PDF только старее по орфографии.\n"
        "Верни ТОЛЬКО JSON без markdown и пояснений:\n"
        '{"edits":[{"old":"…","new":"…","reason":"ocr"}]}\n'
        "Если правок нет: {\"edits\":[]}"
    )


def _system_prompt(target_orthography: str) -> str:
    if target_orthography == "faithful":
        return (
            "Ты корректор русского текста по PDF-источнику. "
            "Исправляй ошибки OCR, сверяясь с PDF буква в букву. "
            "Верни ТОЛЬКО исправленный фрагмент, без пояснений и markdown."
        )
    return (
        "Ты корректор OCR-текста русской книги для печати в современной орфографии.\n\n"
        + _modern_orthography_rules()
        + "\n\n"
        "Задача: исправить опечатки и ошибки распознавания в НАШЕМ тексте. "
        "PDF — только подсказка по смыслу и пропущенным словам; "
        "НИКОГДА не копируй из PDF дореформенные написания (ѣ, ъ, і и т.п.) и не «исправляй» "
        "современное написание под старый PDF. "
        "Не переписывай стиль и не сокращай текст. "
        "Верни ТОЛЬКО исправленный фрагмент, без пояснений и markdown."
    )


def _filter_diff_for_modern(diffs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Убрать из промпта diff, где PDF просто старее нашего текста."""
    kept: List[Dict[str, Any]] = []
    for d in diffs:
        pdf = d.get("pdf") or ""
        ours = d.get("ours") or ""
        if _has_old_orthography(pdf) and not _has_old_orthography(ours):
            continue
        kept.append(d)
    return kept


def _user_prompt(
    our_text: str,
    pdf_text: str,
    *,
    pages: List[int],
    diff: List[Dict[str, Any]],
    target_orthography: str = "modern",
) -> str:
    pages_s = ", ".join(str(p) for p in pages) or "—"
    ours_part = (our_text or "").strip()
    if len(ours_part) > _MAX_OUR_CHARS:
        ours_part = ours_part[:_MAX_OUR_CHARS] + "\n…"
    pdf_part = (pdf_text or "").strip()
    if target_orthography == "modern" and pdf_part:
        pdf_part = modernize_for_prompt_reference(pdf_part)
    if len(pdf_part) > _MAX_PDF_CHARS:
        pdf_part = pdf_part[:_MAX_PDF_CHARS] + "\n…"
    diff_part = _format_diff_for_prompt(
        _filter_diff_for_modern(diff) if target_orthography == "modern" else diff,
        limit=8,
    )
    pdf_note = (
        "Текст PDF (смысл, модернизирован для подсказки — НЕ копировать дословно):\n"
        if target_orthography == "modern"
        else "Текст PDF (эталон по смыслу, орфография PDF может быть старой):\n"
    )
    diff_note = (
        "Расхождения OCR (игнорируй строки, где PDF написан по старым правилам):\n"
        if target_orthography == "modern"
        else "Автоматические расхождения (для ориентира):\n"
    )
    return (
        f"Страницы PDF: {pages_s}\n\n"
        f"Наш текст (исправь его, сохраняя современную орфографию):\n{ours_part}\n\n"
        f"{pdf_note}{pdf_part or '—'}\n\n"
        f"{diff_note}{diff_part}"
    )


def strip_llm_output(text: str) -> str:
    return _strip_noise(text)


def pick_context(
    item: Dict[str, Any],
    *,
    our_text: Optional[str] = None,
    page: Optional[int] = None,
) -> Tuple[str, str, List[int]]:
    if item.get("page") is not None and not item.get("page_slices"):
        page_i = int(item["page"])
        ours = our_text if our_text is not None else (item.get("our_text") or "")
        pdf = item.get("pdf_text") or ""
        return ours, pdf, [page_i]

    ours = our_text if our_text is not None else (item.get("our_text") or "")
    pdf = item.get("pdf_text") or ""
    pages = list(item.get("pages") or [])
    if page is not None:
        pages = [page]
        for sl in item.get("page_slices") or []:
            if int(sl.get("page", 0)) == int(page):
                if not our_text:
                    ours = sl.get("our_text") or ours
                pdf = sl.get("pdf_text") or pdf
                break
    return ours, pdf, pages


def _ollama_chat(
    *,
    system: str,
    user: str,
    model: str,
    base_url: str,
    timeout: float,
    num_predict: int = 2048,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
            "num_predict": num_predict,
        },
    }
    data = _http_json(
        f"{base_url.rstrip('/')}/api/chat",
        payload,
        method="POST",
        timeout=timeout,
    )
    msg = data.get("message") or {}
    raw = msg.get("content") or ""
    if not raw.strip() and msg.get("thinking"):
        raise RuntimeError(
            "Ollama вернул только «thinking», без текста. "
            "Для qwen3/qwen3.5 нужен think=false (уже включён) — обновите Ollama "
            "или используйте instruct-модель (qwen2.5:7b)."
        )
    return raw


def _finalize_suggestion(
    ours: str,
    suggested: str,
    *,
    target_orthography: str,
    model: str,
    mode: str,
    raw: str,
    raw_find: Optional[str] = None,
    edits_found: Optional[List[Dict[str, Any]]] = None,
    edits_applied: Optional[List[Dict[str, Any]]] = None,
    edits_rejected: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    orthography_warning = None
    reverted = False
    if target_orthography == "modern":
        suggested, reverted, sanitize_warn = sanitize_llm_output(suggested, fallback=ours)
        if sanitize_warn:
            orthography_warning = sanitize_warn
        elif _has_old_orthography(suggested) and not _has_old_orthography(ours):
            orthography_warning = (
                "В ответе есть признаки дореформенной орфографии — проверьте вручную."
            )

    return {
        "suggested_text": suggested,
        "model": model,
        "mode": mode,
        "raw": raw,
        "raw_find": raw_find,
        "edits_found": edits_found or [],
        "edits_applied": edits_applied or [],
        "edits_rejected": edits_rejected or [],
        "input_our": ours,
        "changed": suggested != ours,
        "orthography_warning": orthography_warning,
        "reverted_to_original": reverted,
    }


def _suggest_find_apply(
    ours: str,
    pdf: str,
    pages: List[int],
    diff: List[Dict[str, Any]],
    *,
    target_orthography: str,
    model: str,
    base_url: str,
    timeout: float,
    rules_hint: Optional[str],
) -> Dict[str, Any]:
    user_content = _user_prompt(
        ours,
        pdf,
        pages=pages,
        diff=diff,
        target_orthography=target_orthography,
    )
    user_content = (
        "Найди только ошибки OCR в НАШЕМ тексте. Верни JSON edits, не переписывай абзац.\n\n"
        + user_content
    )
    if rules_hint:
        user_content = rules_hint.strip() + "\n\n" + user_content

    raw_find = _ollama_chat(
        system=_system_prompt_find(target_orthography),
        user=user_content,
        model=model,
        base_url=base_url,
        timeout=timeout,
        num_predict=1536,
    )
    edits = parse_edits_json(raw_find)
    source_old = _has_old_orthography(ours)
    apply_result: ApplyResult = apply_edits(
        ours,
        edits,
        source_had_old_orthography=source_old,
    )
    suggested = apply_result.text
    if target_orthography == "modern":
        suggested = modernize_text(suggested)

    edits_found = [e.as_dict() for e in edits]
    return _finalize_suggestion(
        ours,
        suggested,
        target_orthography=target_orthography,
        model=model,
        mode="find_apply",
        raw=raw_find,
        raw_find=raw_find,
        edits_found=edits_found,
        edits_applied=apply_result.applied,
        edits_rejected=apply_result.rejected,
    )


def suggest_correction(
    item: Dict[str, Any],
    *,
    our_text: Optional[str] = None,
    page: Optional[int] = None,
    target_orthography: str = "modern",
    model: str = _DEFAULT_MODEL,
    base_url: str = _DEFAULT_URL,
    timeout: float = _TIMEOUT_S,
    rules_hint: Optional[str] = None,
    mode: str = "find_apply",
) -> Dict[str, Any]:
    if mode not in _OLLAMA_MODES:
        raise ValueError(f"mode должен быть один из {_OLLAMA_MODES}, получено: {mode!r}")

    ours, pdf, pages = pick_context(item, our_text=our_text, page=page)
    if not ours.strip():
        raise ValueError("Пустой текст для корректуры")

    if mode == "find_apply":
        return _suggest_find_apply(
            ours,
            pdf,
            pages,
            item.get("diff") or [],
            target_orthography=target_orthography,
            model=model,
            base_url=base_url,
            timeout=timeout,
            rules_hint=rules_hint,
        )

    user_content = _user_prompt(
        ours,
        pdf,
        pages=pages,
        diff=item.get("diff") or [],
        target_orthography=target_orthography,
    )
    if rules_hint:
        user_content = rules_hint.strip() + "\n\n" + user_content

    raw = _ollama_chat(
        system=_system_prompt(target_orthography),
        user=user_content,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )
    suggested = strip_llm_output(raw)
    if not suggested:
        raise RuntimeError("Ollama вернул пустой ответ")

    return _finalize_suggestion(
        ours,
        suggested,
        target_orthography=target_orthography,
        model=model,
        mode="rewrite",
        raw=raw,
    )
