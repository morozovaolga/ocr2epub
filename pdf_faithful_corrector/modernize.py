# -*- coding: utf-8 -*-
"""Модернизация орфографии (правила из ocr2epub/oldspelling.py + oldspelling_norm)."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

# Символьная нормализация (из tools/oldspelling_norm.py)
_WS = re.compile(r"\s+")
_WORD_FINAL_HARD = re.compile(r"(?<=[А-Яа-яЁё])ъ(?=$|\b)")
_PUNCT_HARD = re.compile(r"(?<=[А-Яа-яЁё])ъ(?=[\s,.:;!?…""»\)\]\-])")
_CHAR_TRANS = str.maketrans({
    "ѣ": "е", "Ѣ": "Е",
    "і": "и", "І": "И",
    "ѳ": "ф", "Ѳ": "Ф",
    "ѵ": "и", "Ѵ": "И",
})

_OLD_ORTHO = re.compile(r"[ѣѢіІѳѲѵѴ]|[а-яёА-ЯЁ]ъ\b")
# Типичные дореформенные окончания, если модель скопировала PDF без ятей
_LEGACY_ENDINGS = re.compile(
    r"\b\w*("
    r"аго|яго|ія|іи|ія|скія|ныя|мыя|"
    r"разс|безс|безп|черезчур|подле|оне|нея|ужь"
    r")\b",
    re.IGNORECASE,
)

_DEFAULT_OLD_SPELLING = Path(__file__).resolve().parent.parent / "assets" / "oldspelling.py"


def normalize_chars(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\u00A0", " ")
    t = t.translate(_CHAR_TRANS)
    t = _WORD_FINAL_HARD.sub("", t)
    t = _PUNCT_HARD.sub("", t)
    return t


def has_old_orthography(text: str) -> bool:
    return bool(_OLD_ORTHO.search(text or "")) or bool(_LEGACY_ENDINGS.search(text or ""))


@lru_cache(maxsize=4)
def _load_lexicon_rules(oldspelling_path: str) -> Tuple[Tuple[str, str], ...]:
    path = Path(oldspelling_path)
    if not path.is_file():
        return _builtin_lexicon()
    rules: List[Tuple[str, str]] = []
    seen = set()
    for m in re.finditer(r"re\.sub\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*html\s*\)", path.read_text(encoding="utf-8")):
        src, dst = m.group(1), m.group(2)
        if src == dst or not src:
            continue
        key = (src, dst)
        if key in seen:
            continue
        seen.add(key)
        rules.append(key)
    if not rules:
        return _builtin_lexicon()
    # длинные фразы первыми
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return tuple(rules)


def _builtin_lexicon() -> Tuple[Tuple[str, str], ...]:
    return (
        (" придти ", " прийти "),
        (" придти,", " прийти,"),
        (" придти.", " прийти."),
        ("каго ", "кого "),
        ("каго,", "кого,"),
        ("каго.", "кого."),
        ("наго ", "ного "),
        ("того ", "того "),
        ("щаго ", "щего "),
        ("черезчур ", "чересчур "),
        (" подле ", " возле "),
        (" оне ", " они "),
        (" ея ", " ее "),
        (" нея ", " нее "),
        (" разс", " расс"),
        (" безс", " бесс"),
        (" безп", " бесп"),
        ("ския ", "ские "),
        ("ныя ", "ные "),
        ("прочия", "прочие"),
        ("многия", "многие"),
        ("как-будто", "как будто"),
        ("что-ж", "что ж"),
    )


def apply_lexicon(text: str, *, oldspelling_path: Path | None = None) -> str:
    path = oldspelling_path or Path(os.environ.get("OCR2EPUB_OLD_SPELLING", str(_DEFAULT_OLD_SPELLING)))
    rules = _load_lexicon_rules(str(path.resolve()))
    t = text
    for src, dst in rules:
        t = t.replace(src, dst)
    return t


def modernize_text(text: str, *, oldspelling_path: Path | None = None) -> str:
    """Полная модернизация: буквы + лексикон oldspelling."""
    t = normalize_chars(text)
    t = apply_lexicon(t, oldspelling_path=oldspelling_path)
    return t


def modernize_for_prompt_reference(pdf_text: str) -> str:
    """PDF для промпта: только смысл, без дореформенных букв."""
    t = modernize_text(pdf_text or "")
    return _WS.sub(" ", t).strip()


def modernize_prompt_block() -> str:
    return (
        "Запрет копирования PDF и дореформенной орфографии:\n"
        "• PDF — источник СМЫСЛА (кто, что, где). Не переноси из PDF буквы, слова и окончания.\n"
        "• В ответе ТОЛЬКО современная орфография (после 1918 г.): е не ѣ, и не і, ф не ѳ, нет ъ на конце слова.\n"
        "• Не используй: -аго/-яго (пиши -ого/-его), -ія/-ыя (пиши -ие/-ые), разс→расс, безс→бесс.\n"
        "• Лексика modernize (как в oldspelling): кого не каго, они не оне, возле не подле, "
        "прийти не придти, чересчур не черезчур, ее не ея.\n"
        "• Если наш текст уже современный, а PDF старее — оставляй наш вариант.\n"
        "• Пунктуация: «ёлочки», тире —, многоточие …; частица ли/ж отделяется пробелом (что ж, если б).\n"
        "• Исправляй только ошибки OCR (опечатки, пропуски, лишние знаки), не стиль автора."
    )


def sanitize_llm_output(
    suggested: str,
    *,
    fallback: str,
    oldspelling_path: Path | None = None,
) -> Tuple[str, bool, str | None]:
    """
    Постобработка ответа LLM. Возвращает (текст, был_откат_к_fallback, предупреждение).
    """
    t = modernize_text(strip_noise(suggested), oldspelling_path=oldspelling_path)
    if has_old_orthography(t):
        return fallback, True, "После модернизации осталась старая орфография — подставлен исходный текст."
    if _looks_like_pdf_copy(t, fallback):
        return fallback, True, "Ответ слишком похож на копию PDF — подставлен исходный текст."
    return t, False, None


def strip_noise(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    for wrap in ('"""', "'''"):
        if t.startswith(wrap) and t.endswith(wrap):
            t = t[3:-3].strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'«»":
        t = t[1:-1].strip()
    if t.lower().startswith("исправленный текст:"):
        t = t.split(":", 1)[1].strip()
    return t


def _looks_like_pdf_copy(suggested: str, ours: str) -> bool:
    """Грубая эвристика: ответ почти дословно старый PDF, а не наш текст."""
    if not suggested or not ours:
        return False
    sm = _similarity(normalize_chars(suggested), normalize_chars(ours))
    # если сильно отличается от ours, но содержит много legacy — уже поймано выше
    if sm < 0.55 and _LEGACY_ENDINGS.search(suggested):
        return True
    return False


def _similarity(a: str, b: str) -> float:
    import difflib
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(a=a, b=b).ratio()
