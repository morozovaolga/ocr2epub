# -*- coding: utf-8 -*-
"""Правила, выученные из ручных правок корректора."""
from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

_OLD_ORTHO = re.compile(r"[ѣѢіІѳѲ]|[а-яёА-ЯЁ]ъ\b")

# Только явная ручная правка текста (не accept_llm / keep_ours).
HUMAN_EDIT_ACTIONS = frozenset({"edit"})

_MAX_RULE_LEN = 80


def _has_old_orthography(s: str) -> bool:
    return bool(_OLD_ORTHO.search(s or ""))


def extract_replacements(before: str, after: str) -> List[Tuple[str, str]]:
    """Токенные замены before→after (только replace)."""
    bw = (before or "").split()
    aw = (after or "").split()
    if not bw or not aw:
        return []
    out: List[Tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=bw, b=aw).get_opcodes():
        if tag != "replace":
            continue
        src = " ".join(bw[i1:i2]).strip()
        dst = " ".join(aw[j1:j2]).strip()
        if not src or not dst or src == dst:
            continue
        out.append((src, dst))
    return out


def _bundled_rules_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "rules" / "ocr"


def ocr_rules_dir() -> Path:
    env = os.environ.get("OCR2EPUB_RULES") or os.environ.get("BPP_RULES")
    if env:
        return Path(env)
    bundled = _bundled_rules_dir()
    if bundled.is_dir():
        return bundled
    root = Path(
        os.environ.get(
            "OCR2EPUB_ROOT",
            Path(__file__).resolve().parent.parent.parent / "ocr2epub",
        )
    )
    return root / "rules" / "ocr"


def global_learned_path() -> Path:
    """Общая база ручных замен для всех книг (bpp apply читает dictionaries/*.jsonl)."""
    return ocr_rules_dir() / "dictionaries" / "human_learned.jsonl"


def _rule_pairs_from_diff(
    before: str,
    after: str,
    *,
    source: str,
    extra: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    if not after or before == after:
        return []
    meta = dict(extra or {})
    out: List[Dict[str, str]] = []
    for src, dst in extract_replacements(before, after):
        if _has_old_orthography(dst) and not _has_old_orthography(src):
            continue
        if len(src) > _MAX_RULE_LEN or len(dst) > _MAX_RULE_LEN:
            continue
        row = {"from": src, "to": dst, "source": source, **meta}
        out.append(row)
    return out


def harvest_from_record(
    record: Dict,
    *,
    human_only: bool = False,
) -> List[Dict[str, str]]:
    """Словарные пары из одной записи training log."""
    action = record.get("action")
    if human_only:
        if action not in HUMAN_EDIT_ACTIONS:
            return []
    elif action not in ("edit", "accept_llm"):
        return []
    extra = {
        "paragraph_index": record.get("paragraph_index"),
        "at": record.get("at"),
    }
    block_ids = record.get("block_ids") or []
    if block_ids:
        extra["block_id"] = block_ids[0]
    return _rule_pairs_from_diff(
        record.get("our_text") or "",
        record.get("corrected_text") or "",
        source="human_edit" if human_only else "corrector_human_edit",
        extra=extra,
    )


def harvest_from_records(
    records: Iterable[Dict],
    *,
    human_only: bool = False,
) -> List[Dict[str, str]]:
    """Собрать словарные пары из training log (без отката к дореформ.)."""
    seen: Set[Tuple[str, str]] = set()
    rules: List[Dict[str, str]] = []
    for rec in records:
        for row in harvest_from_record(rec, human_only=human_only):
            key = (row["from"], row["to"])
            if key in seen:
                continue
            seen.add(key)
            rules.append(row)
    return rules


def merge_record_to_global(
    record: Dict,
    global_path: Optional[Path] = None,
    *,
    book: str = "",
) -> int:
    """Дописать в общую базу пары из одной ручной правки."""
    path = global_path or global_learned_path()
    rules = harvest_from_record(record, human_only=True)
    if not rules:
        return 0
    if book:
        for r in rules:
            r["book"] = book
    return append_rules_jsonl(path, rules)


def merge_training_log_to_global(
    training_path: Path,
    *,
    book: str = "",
    global_path: Optional[Path] = None,
) -> int:
    """Дописать в общую базу все ручные правки из corrector_training*.jsonl."""
    if not training_path.is_file():
        return 0
    records = []
    for line in training_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    rules = harvest_from_records(records, human_only=True)
    if book:
        for r in rules:
            r.setdefault("book", book)
    return append_rules_jsonl(global_path or global_learned_path(), rules)


def load_merged_rules_jsonl(*paths: Path) -> List[Tuple[str, str]]:
    """Объединить несколько jsonl без дубликатов (порядок файлов сохраняется)."""
    out: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for path in paths:
        for pair in load_rules_jsonl(path):
            if pair in seen:
                continue
            seen.add(pair)
            out.append(pair)
    return out


def load_rules_jsonl(path: Path) -> List[Tuple[str, str]]:
    if not path.is_file():
        return []
    out: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        j = json.loads(line)
        frm, to = j.get("from"), j.get("to")
        if not isinstance(frm, str) or not isinstance(to, str) or not frm or not to:
            continue
        key = (frm, to)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def append_rules_jsonl(path: Path, rules: List[Dict[str, str]]) -> int:
    existing = {(a, b) for a, b in load_rules_jsonl(path)}
    added = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rules:
            key = (r["from"], r["to"])
            if key in existing:
                continue
            existing.add(key)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            added += 1
    return added


def apply_word_rules(text: str, rules: List[Tuple[str, str]]) -> str:
    for frm, to in rules:
        pat = r"\b" + re.escape(frm) + r"\b"
        text = re.sub(pat, to, text)
    return text


def format_rules_for_prompt(rules: List[Tuple[str, str]], *, limit: int = 30) -> str:
    """Краткий список выученных замен для LLM-промпта."""
    if not rules:
        return ""
    lines = [f"- «{frm}» → «{to}»" for frm, to in rules[:limit]]
    tail = f"\n- … ещё {len(rules) - limit} правил" if len(rules) > limit else ""
    return "Выученные замены из ручной корректуры (применяй при совпадении):\n" + "\n".join(lines) + tail


def harvest_and_merge_training_log(
    training_path: Path,
    rules_out: Path,
) -> int:
    if not training_path.is_file():
        return 0
    records = []
    for line in training_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    new_rules = harvest_from_records(records, human_only=False)
    return append_rules_jsonl(rules_out, new_rules)
