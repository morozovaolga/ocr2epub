"""
Разбиение склеенных слов (word splitting / reverse despacing).

Типичный артефакт OCR: "АфанасийНикитин" вместо "Афанасий Никитин",
"ИзКолязина" вместо "Из Колязина", "ПОИДОХНАУГЛ" вместо "ПОИДОХ НА УГЛЕЧЬ".

Алгоритм: DP-сегментация на уровне символов с валидацией через pymorphy2 + navec.
Смена регистра (lower→Upper, lower→CAPS) — сильный сигнал границы слова.
"""

import argparse
import inspect
import math
import os
import re
from pathlib import Path
from html import escape as hesc
from typing import Optional

# Обходной путь для совместимости с Python 3.11+
if not hasattr(inspect, "getargspec"):
    def _getargspec(func):
        spec = inspect.getfullargspec(func)
        return spec.args, spec.varargs, spec.varkw, spec.defaults
    inspect.getargspec = _getargspec  # type: ignore[attr-defined]

try:
    from pymorphy2 import MorphAnalyzer
    MORPH_AVAILABLE = True
except (ImportError, Exception):
    MORPH_AVAILABLE = False
    MorphAnalyzer = None

# ---------------------------------------------------------------------------
# Navec — компактные русские эмбеддинги (500K слов, ~50 МБ)
# ---------------------------------------------------------------------------
NAVEC_AVAILABLE = False
_navec_model = None


def _load_navec():
    """Ленивая загрузка модели navec."""
    global NAVEC_AVAILABLE, _navec_model
    if _navec_model is not None:
        return _navec_model
    try:
        from navec import Navec
        home = os.path.expanduser("~")
        model_path = os.path.join(home, ".navec", "navec_hudlit_v1_12B_500K_300d_100q.tar")
        if not os.path.exists(model_path):
            import urllib.request
            url = "https://storage.yandexcloud.net/natasha-navec/packs/navec_hudlit_v1_12B_500K_300d_100q.tar"
            os.makedirs(os.path.dirname(model_path), exist_ok=True)
            print("  Скачиваю navec модель (~50 МБ)...", end=" ", flush=True)
            urllib.request.urlretrieve(url, model_path)
            print("OK")
        _navec_model = Navec.load(model_path)
        NAVEC_AVAILABLE = True
        return _navec_model
    except Exception:
        NAVEC_AVAILABLE = False
        _navec_model = False
        return None


def _in_navec(word: str, _cache: dict = {}) -> bool:
    """Проверяет наличие слова в навеке (с учётом ъ)."""
    navec = _load_navec()
    if not navec:
        return False
    wl = word.lower()
    if wl in _cache:
        return _cache[wl]
    result = wl in navec
    if not result and wl.endswith('ъ') and len(wl) > 2:
        result = wl[:-1] in navec
    _cache[wl] = result
    return result


# ---------------------------------------------------------------------------
# Словарные проверки
# ---------------------------------------------------------------------------
_CYR = re.compile(r'^[А-ЯЁа-яё]+$')

OLD_RUSSIAN_WORDS = {
    "есмя", "есми", "есмь", "поидох", "поидохом", "приидох", "внидох",
    "бых", "быхом", "идох", "взях", "видех", "обретох", "рекох",
    "пошли", "пошел", "поехал", "поехали", "приехал", "приехали",
    "взяли", "взял", "застрелили", "застрелял", "поймали",
    "велми", "вельми", "зело", "токмо", "паки", "понеже", "аще",
    "инде", "зане", "занеже", "убо", "яко", "ино",
    "град", "грады", "земля", "земли", "перец", "мечь", "путь",
    "язь", "азъ", "аз", "мя", "тя", "ся",
    "султан", "салтан", "хан", "бег", "бек", "возырь", "возыри",
    "мелик", "тучар", "шах", "ширваншах",
    "бедер", "бедери", "бедеря", "чюнер", "чюнере", "чюнеря",
    "гурмыз", "гурмыза", "гурмызе", "ормус", "ормуса",
    "дабыл", "дабыла", "дабыли", "келекот", "келекота",
    "гундустан", "гундустани", "гундустаньский",
    "хоросань", "хоросанский", "хоросанец", "хоросанцы",
    "бесермен", "бесермены", "бесерменский", "бесерменьский",
    "бесерменьской", "бесерменьская", "бесерменьское", "бесерменьские",
    "бесерменскую", "бесерменском", "бесерменским", "бесерменскими",
    "бысть", "быти", "бяше", "бяху", "рече", "рекоша",
    "сказа", "глагола", "глаголя", "глаголаше",
    "хожаше", "хождаше", "хожение", "хождение",
    "живяше", "стояше", "бояше", "идяше", "идяху", "имяше",
    "приидоша", "поидоша",
    "великомъ", "княжении", "тверскомъ",
    "нет", "что", "это", "они", "его", "она", "тот",
    "все", "как", "был", "для", "или", "при",
    "фота", "алафу", "тенекь", "ковов", "тенек",
    "осподарыни", "осподарь",
    "въ", "изъ", "къ", "подъ", "надъ", "предъ", "объ",
}


def _is_cyr(s: str) -> bool:
    return bool(_CYR.match(s))


def _is_dict_word(word: str, morph, _cache: dict = {}) -> bool:
    """Слово реально в словаре pymorphy2 (DictionaryAnalyzer), с учётом ъ."""
    if not morph:
        return False
    wl = word.lower()
    if wl in _cache:
        return _cache[wl]
    if wl in OLD_RUSSIAN_WORDS:
        _cache[wl] = True
        return True
    if wl and wl[0] in 'ьъ':
        _cache[wl] = False
        return False

    def _check(w):
        try:
            p = morph.parse(w)
            if not p or not p[0].methods_stack:
                return False
            return type(p[0].methods_stack[0][0]).__name__ == "DictionaryAnalyzer"
        except Exception:
            return False

    result = _check(wl)
    if not result and wl.endswith('ъ') and len(wl) > 2:
        result = _check(wl[:-1])
    _cache[wl] = result
    return result


# ---------------------------------------------------------------------------
# Определение кандидатов на разбиение
# ---------------------------------------------------------------------------

def _has_case_transition(token: str) -> bool:
    """Есть ли внутри токена переход lower→Upper (camelCase / склейка)?

    Примеры: АфанасийНикитин → True, ГУРМЫЗ → False, путешествие → False
    """
    for k in range(1, len(token)):
        if token[k - 1].islower() and token[k].isupper():
            return True
    return False


def _find_case_boundaries(token: str) -> list[int]:
    """Возвращает индексы символов, где lower→Upper (потенциальные границы слов).

    Для "АфанасийНикитин" вернёт [8] (индекс 'Н').
    Для "ИзКолязинаПОИДОХ" вернёт [2, 10].
    Для "градаБедеря" вернёт [5].
    """
    boundaries = []
    for k in range(1, len(token)):
        if token[k - 1].islower() and token[k].isupper():
            boundaries.append(k)
    return boundaries


def _is_split_candidate(token: str, morph) -> bool:
    """Нужно ли пытаться разбить этот токен?

    Кандидаты:
    1. Содержат переход lower→Upper (АфанасийНикитин, ИзКолязина)
    2. Длинные (>8 символов) и не в словаре — вероятно склеенные слова
    3. Полностью CAPS и длинные (>12) — могут быть склеенные слова
    """
    if not _is_cyr(token):
        return False

    clen = len(token)

    if _has_case_transition(token):
        return True

    if token.isupper() and clen > 12:
        if not _in_navec(token):
            return True

    if clen > 8:
        if not _is_dict_word(token, morph) and not _in_navec(token):
            return True

    return False


# ---------------------------------------------------------------------------
# DP-алгоритм разбиения склеенных слов
# ---------------------------------------------------------------------------

# Однобуквенные слова, которые могут стоять отдельно
_SINGLE_CHAR_WORDS = {'в', 'с', 'к', 'о', 'у', 'я', 'а', 'и'}

# Двубуквенные предлоги / частицы
_SHORT_WORDS = {
    'на', 'за', 'по', 'от', 'из', 'до', 'во', 'об', 'ко', 'ни',
    'не', 'да', 'же', 'ли', 'бы', 'то', 'но', 'ту', 'ся',
    'их', 'он', 'ей', 'ем', 'ее', 'им', 'ни', 'ну',
    'мы', 'ты', 'вы', 'вс',
}


def _word_score(word: str, char_len: int, morph) -> float:
    """Оценка качества слова-кандидата при разбиении.

    Возвращает морфологический бонус.
    """
    wl = word.lower()
    is_dict = _is_dict_word(word, morph)
    in_navec = _in_navec(word)
    is_old_ru = wl in OLD_RUSSIAN_WORDS

    if char_len <= 1:
        if wl in _SINGLE_CHAR_WORDS:
            return 1.5
        return -1.0  # Штраф за бессмысленный однобуквенный фрагмент

    if char_len <= 2:
        if wl in _SHORT_WORDS or is_old_ru:
            return 2.0
        if is_dict and in_navec:
            return 1.5
        if is_dict:
            return 0.8
        return -0.5  # Штраф за неизвестный двубуквенный фрагмент

    if is_old_ru:
        return 3.0

    if is_dict and in_navec:
        if char_len >= 6:
            return 3.5
        return 2.5
    if in_navec:
        if char_len >= 6:
            return 3.0
        return 2.0
    if is_dict:
        if char_len >= 6:
            return 1.5
        return 1.0

    return -0.3  # Незнакомое слово — штраф


def _dp_split(text: str, morph, case_bonus_positions: set = None) -> list[str]:
    """DP-оптимальное разбиение строки символов на слова.

    case_bonus_positions — набор позиций, где есть переход lower→Upper;
    разрез в такой позиции получает бонус вместо штрафа.

    Возвращает список слов.
    """
    n = len(text)
    if n == 0:
        return []
    if n <= 3:
        return [text]

    if case_bonus_positions is None:
        case_bonus_positions = set()

    INF = float('-inf')
    MAX_WORD = min(n, 30)  # максимальная длина слова

    SPLIT_PENALTY = -2.5
    CASE_SPLIT_BONUS = 3.0  # бонус за разрез в точке смены регистра

    # dp[i] = (лучший_score, предыдущая_позиция)
    dp = [(INF, -1)] * (n + 1)
    dp[0] = (0.0, -1)

    for i in range(1, n + 1):
        for length in range(1, min(i, MAX_WORD) + 1):
            j = i - length
            if dp[j][0] == INF:
                continue

            candidate = text[j:i]
            char_len = length

            morph_bonus = _word_score(candidate, char_len, morph)
            length_bonus = char_len * 0.25 + math.sqrt(char_len) * 0.25

            if j > 0:
                if j in case_bonus_positions:
                    penalty = SPLIT_PENALTY + CASE_SPLIT_BONUS
                else:
                    penalty = SPLIT_PENALTY
            else:
                penalty = 0

            total = dp[j][0] + morph_bonus + length_bonus + penalty
            if total > dp[i][0]:
                dp[i] = (total, j)

    if dp[n][0] == INF:
        return [text]

    words = []
    pos = n
    while pos > 0:
        prev = dp[pos][1]
        words.append(text[prev:pos])
        pos = prev
    words.reverse()
    return words


def _restore_case(original: str, words: list[str]) -> list[str]:
    """Восстанавливает оригинальный регистр символов из исходной строки.

    words — результат DP (в оригинальном регистре, просто нарезка).
    Если вся исходная строка была CAPS, возвращает слова КАПСом.
    """
    return words  # DP работает на оригинале, регистр сохраняется


# ---------------------------------------------------------------------------
# Обработка CAPS-блоков
# ---------------------------------------------------------------------------

def _split_caps_block(text: str, morph) -> str:
    """Разбивает полностью CAPS-текст на слова.

    Для полностью заглавного текста нет сигналов смены регистра,
    поэтому используем чистый DP на lowercase, а потом восстанавливаем CAPS.
    """
    if len(text) <= 5:
        return text

    lower = text.lower()
    words_lower = _dp_split(lower, morph, set())

    result = []
    pos = 0
    for w in words_lower:
        wlen = len(w)
        result.append(text[pos:pos + wlen])
        pos += wlen

    return " ".join(result)


# ---------------------------------------------------------------------------
# Основная логика разбиения токена
# ---------------------------------------------------------------------------

def split_token(token: str, morph) -> str:
    """Разбивает один склеенный токен на слова, расставляя пробелы."""
    if len(token) <= 3:
        return token

    if not _is_cyr(token):
        return token

    # 1) Если всё целое слово в словаре / навеке — не трогаем
    if _is_dict_word(token, morph) or _in_navec(token):
        return token

    # 2) Есть переходы lower→Upper — разрезаем по ним сначала,
    #    потом каждый кусок проверяем / дробим дальше
    boundaries = _find_case_boundaries(token)
    if boundaries:
        return _split_by_case_then_dp(token, boundaries, morph)

    # 3) Полностью CAPS и длинный — DP на символах
    if token.isupper() and len(token) > 12:
        return _split_caps_block(token, morph)

    # 4) Длинный / не в словаре — DP
    if len(token) > 8:
        case_pos = set(_find_case_boundaries(token))
        words = _dp_split(token, morph, case_pos)
        if len(words) > 1:
            # Проверяем, что разбиение осмысленно: хотя бы одно слово
            # из результата должно быть в словаре
            any_known = any(
                _is_dict_word(w, morph) or _in_navec(w)
                for w in words if len(w) > 2
            )
            if any_known:
                return " ".join(words)

    return token


def _split_by_case_then_dp(token: str, boundaries: list[int], morph) -> str:
    """Разрезает по границам регистра, потом каждый сегмент проверяет.

    Для "АфанасийНикитин" с boundary=[8]:
      → ["Афанасий", "Никитин"] — оба в словаре → "Афанасий Никитин"

    Для "ИзКолязинаПОИДОХ" с boundaries=[2, 10]:
      → ["Из", "Колязина", "ПОИДОХ"] — все в словаре → "Из Колязина ПОИДОХ"

    Если сегмент сам по себе не в словаре и длинный — применяем DP к нему.
    """
    cuts = [0] + boundaries + [len(token)]
    segments = [token[cuts[k]:cuts[k + 1]] for k in range(len(cuts) - 1)]

    result_parts = []
    for seg in segments:
        if not seg:
            continue

        # Сегмент — целое словарное слово? Оставляем как есть.
        if _is_dict_word(seg, morph) or _in_navec(seg):
            result_parts.append(seg)
            continue

        # Полностью CAPS — DP
        if seg.isupper() and len(seg) > 6:
            result_parts.append(_split_caps_block(seg, morph))
            continue

        # Длинный смешанный сегмент — DP
        if len(seg) > 8 and not _is_dict_word(seg, morph) and not _in_navec(seg):
            inner_boundaries = set(_find_case_boundaries(seg))
            words = _dp_split(seg, morph, inner_boundaries)
            if len(words) > 1:
                result_parts.append(" ".join(words))
                continue

        result_parts.append(seg)

    return " ".join(result_parts)


# ---------------------------------------------------------------------------
# Обработка целой строки / текста
# ---------------------------------------------------------------------------

def split_line(line: str, morph) -> str:
    """Обрабатывает одну строку: разбивает склеенные токены."""
    if not line.strip():
        return line

    parts = re.findall(r'[А-ЯЁа-яё]+|[^А-ЯЁа-яё]+', line)
    result = []

    for part in parts:
        if _is_cyr(part) and _is_split_candidate(part, morph):
            result.append(split_token(part, morph))
        else:
            result.append(part)

    return "".join(result)


def split_words(text: str) -> str:
    """Разбивает склеенные слова во всём тексте.

    Главная функция модуля. Обрабатывает построчно.
    """
    morph = None
    if MORPH_AVAILABLE:
        try:
            morph = MorphAnalyzer()
        except Exception:
            morph = None

    lines = text.split('\n')
    result = []
    total_splits = 0

    for line in lines:
        original_spaces = line.count(' ')
        processed = split_line(line, morph)
        new_spaces = processed.count(' ')
        total_splits += new_spaces - original_spaces
        result.append(processed)

    if total_splits > 0:
        print(f"  Добавлено пробелов: {total_splits}")

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# HTML / CLI
# ---------------------------------------------------------------------------

def to_html(text: str, title: str) -> str:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "\n".join(f"<p>{hesc(p)}</p>" for p in paras)
    return (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        f"<title>{hesc(title)}</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        "<style>body{font:18px/1.6 Georgia,Times,\"Times New Roman\",serif;"
        "margin:2rem;max-width:48rem;color:#111;background:#fff} "
        "p{margin:0 0 1rem}</style>\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def main():
    ap = argparse.ArgumentParser(
        description="Разбиение склеенных слов (word splitting). "
                    "Для OCR-текстов, где слова слиплись без пробелов."
    )
    ap.add_argument("--in", dest="inp", required=True, help="Входной TXT")
    ap.add_argument("--out", dest="out", required=True, help="Выходной TXT")
    ap.add_argument("--html", dest="html", help="Необязательный путь для HTML")
    ap.add_argument("--title", default="После разбиения слов", help="Заголовок HTML")
    args = ap.parse_args()

    src = Path(args.inp)
    dst = Path(args.out)
    text = src.read_text(encoding="utf-8", errors="replace")

    print(f"Входной текст: {len(text)} символов")
    result = split_words(text)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(result, encoding="utf-8")
    if args.html:
        Path(args.html).write_text(to_html(result, args.title), encoding="utf-8")
    print(f"Сохранено: {dst}" + (f", {args.html}" if args.html else ""))


if __name__ == "__main__":
    main()
