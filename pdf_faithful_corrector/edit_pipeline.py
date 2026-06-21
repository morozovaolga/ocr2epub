# -*- coding: utf-8 -*-
"""Двухфазная корректура: JSON-список правок → безопасное применение."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .modernize import has_old_orthography, modernize_text, strip_noise

_MAX_EDIT_OLD = 120
_MAX_EDITS = 40


@dataclass
class TextEdit:
    old: str
    new: str
    reason: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"old": self.old, "new": self.new, "reason": self.reason}


@dataclass
class ApplyResult:
    text: str
    applied: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)


def _extract_json_blob(raw: str) -> str:
    t = strip_noise(raw or "")
    if not t:
        return ""
    # ```json ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # первый объект или массив
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start >= 0:
            depth = 0
            for i in range(start, len(t)):
                if t[i] == opener:
                    depth += 1
                elif t[i] == closer:
                    depth -= 1
                    if depth == 0:
                        return t[start : i + 1]
    return t


def parse_edits_json(raw: str) -> List[TextEdit]:
    blob = _extract_json_blob(raw)
    if not blob:
        return []
    data = json.loads(blob)
    items: List[Any]
    if isinstance(data, dict):
        items = data.get("edits") or data.get("changes") or data.get("corrections") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    out: List[TextEdit] = []
    for item in items[:_MAX_EDITS]:
        if not isinstance(item, dict):
            continue
        old = str(item.get("old") or item.get("from") or item.get("before") or "").strip()
        new = str(item.get("new") or item.get("to") or item.get("after") or "").strip()
        reason = str(item.get("reason") or item.get("type") or "").strip()
        if not old or old == new:
            continue
        out.append(TextEdit(old=old, new=new, reason=reason))
    return out


def _validate_edit(
    edit: TextEdit,
    text: str,
    *,
    source_had_old_orthography: bool,
) -> Optional[str]:
    if len(edit.old) > _MAX_EDIT_OLD:
        return "слишком длинный фрагмент old"
    if edit.old not in text:
        return "old не найден в тексте"
    count = text.count(edit.old)
    if count > 1:
        return f"old встречается {count} раз — неоднозначно"
    if has_old_orthography(edit.new) and not source_had_old_orthography:
        return "new содержит дореформенную орфографию"
    if has_old_orthography(edit.old) and not source_had_old_orthography:
        return "нельзя заменять на старую орфографию (old устарел)"
    # слишком крупная перестройка одной правкой
    if len(edit.old) > max(80, len(text) // 2):
        return "правка затрагивает слишком большой фрагмент"
    return None


def apply_edits(
    text: str,
    edits: List[TextEdit],
    *,
    source_had_old_orthography: bool = False,
) -> ApplyResult:
    """Применить правки: длинные фрагменты первыми, по одному вхождению."""
    current = text
    applied: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    ordered = sorted(edits, key=lambda e: len(e.old), reverse=True)
    for edit in ordered:
        err = _validate_edit(edit, current, source_had_old_orthography=source_had_old_orthography)
        if err:
            rejected.append({**edit.as_dict(), "error": err})
            continue
        current = current.replace(edit.old, edit.new, 1)
        applied.append(edit.as_dict())
    return ApplyResult(text=current, applied=applied, rejected=rejected)
