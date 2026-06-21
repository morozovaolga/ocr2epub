# -*- coding: utf-8 -*-
"""Орфография современного русского (Yandex.Speller) для UI review."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib import error as urlerror
from urllib import parse, request

_FN = re.compile(r"\{\{fn:\d+\}\}")
_WORD = re.compile(r"^[A-Za-zА-Яа-яЁё\-]+$", re.UNICODE)


def prepare_text_for_spell(text: str) -> str:
    """Сохранить длину строки: сноски {{fn:N}} → пробелы."""
    return _FN.sub(lambda m: " " * len(m.group()), text or "")


def _should_highlight_bad_form(frm: str) -> bool:
    s = (frm or "").strip()
    if not s or len(s) > 80:
        return False
    if not re.search(r"[A-Za-zА-Яа-яЁё0-9]", s):
        return False
    if " " in s:
        return len(s) >= 4
    return len(s) >= 3


def _rules_dir() -> Path:
    env = os.environ.get("OCR2EPUB_RULES") or os.environ.get("BPP_RULES")
    if env:
        return Path(env)
    bundled = Path(__file__).resolve().parent.parent / "assets" / "rules" / "ocr"
    if bundled.is_dir():
        return bundled
    ocr_root = Path(
        os.environ.get("OCR2EPUB_ROOT", Path(__file__).resolve().parent.parent.parent / "ocr2epub")
    )
    return ocr_root / "rules" / "ocr"


def _learned_rules_paths(workdir: Optional[Path] = None) -> List[Path]:
    rules_dir = _rules_dir()
    paths = [rules_dir / "dictionaries" / "human_learned.jsonl"]
    if workdir:
        local = workdir / "rules_learned.jsonl"
        if local.is_file():
            paths.append(local)
    return paths


def load_learned_bad_pairs(workdir: Optional[Path] = None) -> List[Dict[str, str]]:
    """Неправильные формы (from) → подсказка (to) из human_learned + rules_learned книги."""
    priority = {
        "human_edit": 3,
        "corrector_human_edit": 3,
        "llm_find_apply": 1,
    }
    best: Dict[str, Tuple[int, str]] = {}

    for path in _learned_rules_paths(workdir):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            frm = j.get("from") or j.get("old") or j.get("src")
            to = j.get("to") or j.get("new") or j.get("dst")
            if not isinstance(frm, str) or not isinstance(to, str):
                continue
            frm = frm.strip()
            to = to.strip()
            if not frm or not to or frm == to:
                continue
            if not _should_highlight_bad_form(frm):
                continue
            src = str(j.get("source") or "")
            pr = priority.get(src, 2)
            prev = best.get(frm)
            if prev is None or pr >= prev[0]:
                best[frm] = (pr, to)

    return [{"from": frm, "to": to} for frm, (_pr, to) in sorted(best.items(), key=lambda x: -len(x[0]))]


def _ranges_overlap(a_start: int, a_end: int, used: List[Tuple[int, int]]) -> bool:
    for b_start, b_end in used:
        if a_start < b_end and a_end > b_start:
            return True
    return False


def find_learned_errors(text: str, pairs: List[Dict[str, str]]) -> List[Dict]:
    if not text or not pairs:
        return []
    used: List[Tuple[int, int]] = []
    errors: List[Dict] = []
    for pair in pairs:
        frm = pair.get("from") or ""
        to = pair.get("to") or ""
        if not frm:
            continue
        start = 0
        while True:
            idx = text.find(frm, start)
            if idx == -1:
                break
            end = idx + len(frm)
            if not _ranges_overlap(idx, end, used):
                errors.append({
                    "pos": idx,
                    "len": len(frm),
                    "word": text[idx:end],
                    "suggestions": [to] if to else [],
                    "kind": "learned",
                })
                used.append((idx, end))
            start = idx + max(len(frm), 1)
    errors.sort(key=lambda e: e["pos"])
    return errors


def merge_highlight_errors(learned: List[Dict], spell: List[Dict]) -> List[Dict]:
    """Объединить подсветку: learned имеет приоритет над spell при перекрытии."""
    learned = list(learned)
    spell = list(spell)
    drop: Set[int] = set()
    for i, sp in enumerate(spell):
        sp_s, sp_e = sp["pos"], sp["pos"] + sp["len"]
        for lr in learned:
            lr_s, lr_e = lr["pos"], lr["pos"] + lr["len"]
            if sp_s < lr_e and sp_e > lr_s:
                drop.add(i)
                break
    out = [{**e, "kind": "learned"} for e in learned]
    for i, sp in enumerate(spell):
        if i not in drop:
            out.append({**sp, "kind": "spell"})
    out.sort(key=lambda e: e["pos"])
    return out


def load_whitelist_words(workdir: Optional[Path] = None) -> Set[str]:
    """Слова, которые не помечать ошибкой (правильные формы из learned rules)."""
    words: Set[str] = set()

    def _read_jsonl(path: Path) -> None:
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("to", "dst", "new"):
                val = j.get(key)
                if isinstance(val, str):
                    for w in val.split():
                        if _WORD.match(w):
                            words.add(w.lower())

    rules_dir = _rules_dir()
    _read_jsonl(rules_dir / "dictionaries" / "human_learned.jsonl")
    _read_jsonl(rules_dir / "whitelist.jsonl")

    if workdir:
        _read_jsonl(workdir / "rules_learned.jsonl")
        book_json = workdir / "book.json"
        if book_json.is_file():
            try:
                meta = json.loads(book_json.read_text(encoding="utf-8"))
                for key in ("title", "author"):
                    val = meta.get(key)
                    if isinstance(val, str):
                        for w in val.split():
                            if _WORD.match(w):
                                words.add(w.lower())
            except json.JSONDecodeError:
                pass

    return words


class YandexSpellChecker:
    url = "https://speller.yandex.net/services/spellservice.json/checkText"

    def __init__(self, lang: str = "ru", timeout: float = 15.0):
        self.lang = lang
        self.timeout = timeout

    def check(self, text: str) -> List[Dict]:
        if not (text or "").strip():
            return []
        data = parse.urlencode({
            "text": text,
            "lang": self.lang,
            "options": 0,
        }).encode("utf-8")
        req = request.Request(
            self.url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent": "pdf_faithful_corrector/spellcheck",
            },
        )
        with request.urlopen(req, timeout=self.timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
        entries = json.loads(payload)
        out: List[Dict] = []
        for entry in entries or []:
            pos = int(entry.get("pos") or 0)
            length = int(entry.get("len") or 0)
            word = (entry.get("word") or text[pos : pos + length]).strip()
            suggestions = [s for s in (entry.get("s") or []) if isinstance(s, str)][:5]
            out.append({
                "pos": pos,
                "len": length,
                "word": word,
                "suggestions": suggestions,
            })
        return out


def filter_spell_errors(
    text: str,
    raw_errors: List[Dict],
    whitelist: Set[str],
) -> List[Dict]:
    out: List[Dict] = []
    seen: Set[tuple] = set()
    for err in raw_errors:
        pos = int(err.get("pos") or 0)
        length = int(err.get("len") or 0)
        if length <= 0 or pos < 0 or pos + length > len(text):
            continue
        fragment = text[pos : pos + length]
        key = (pos, length, fragment)
        if key in seen:
            continue
        seen.add(key)
        core = fragment.strip(".,;:!?«»\"'()—–-…")
        if not core or core.isdigit():
            continue
        if core.lower() in whitelist:
            continue
        out.append({
            "pos": pos,
            "len": length,
            "word": fragment,
            "suggestions": err.get("suggestions") or [],
            "kind": "spell",
        })
    return out


@lru_cache(maxsize=1)
def default_checker() -> YandexSpellChecker:
    return YandexSpellChecker()


def spellcheck_text(
    text: str,
    *,
    workdir: Optional[Path] = None,
    checker: Optional[YandexSpellChecker] = None,
    learned_pairs: Optional[List[Dict[str, str]]] = None,
) -> Dict:
    pairs = learned_pairs if learned_pairs is not None else load_learned_bad_pairs(workdir)
    learned_errors = find_learned_errors(text, pairs)
    prepared = prepare_text_for_spell(text)
    checker = checker or default_checker()
    whitelist = load_whitelist_words(workdir)
    try:
        raw = checker.check(prepared)
    except (urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return {
            "ok": False,
            "error": str(e),
            "errors": learned_errors,
            "spell_errors": [],
            "learned_errors": learned_errors,
            "error_count": len(learned_errors),
        }
    spell_errors = filter_spell_errors(text, raw, whitelist)
    merged = merge_highlight_errors(learned_errors, spell_errors)
    return {
        "ok": True,
        "errors": merged,
        "spell_errors": spell_errors,
        "learned_errors": learned_errors,
        "error_count": len(merged),
        "learned_count": len(learned_errors),
        "spell_count": len(spell_errors),
    }
