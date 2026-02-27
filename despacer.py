"""
Агрессивная склейка разорванных пробелами слов (despacing).

Типичный артефакт OCR старопечатных сканов: "п у т е ш е с т вия" вместо "путешествия".
Алгоритм: DP-сегментация на уровне токенов с валидацией через pymorphy2 + navec.
"""

import argparse
import inspect
import os
import re
import unicodedata
from pathlib import Path
from html import escape as hesc
from typing import Optional

# Обходной путь для совместимости с Python 3.11+ (pymorphy2 использует устаревший inspect.getargspec)
if not hasattr(inspect, "getargspec"):
    def _getargspec(func):
        spec = inspect.getfullargspec(func)
        return spec.args, spec.varargs, spec.varkw, spec.defaults
    inspect.getargspec = _getargspec  # type: ignore[attr-defined]

try:
    from pymorphy2 import MorphAnalyzer
    MORPH_AVAILABLE = True
except ImportError:
    MORPH_AVAILABLE = False
    MorphAnalyzer = None
except Exception:
    MORPH_AVAILABLE = False
    MorphAnalyzer = None

# Navec — компактные русские эмбеддинги (500K слов, ~50 МБ)
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
        _navec_model = False  # sentinel: tried and failed
        return None


def _in_navec(word: str, _cache: dict = {}) -> bool:
    """Проверяет, есть ли слово в словаре navec (500K слов).
    
    Также пробует вариант без концевого ъ (дореволюционная орфография).
    """
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

# Кириллические буквы (включая Ё)
_CYR = re.compile(r'^[А-ЯЁа-яё]+$')

# Древнерусская / архаичная лексика, которая не распознаётся pymorphy2,
# но является валидными словами и не должна быть разрушена
OLD_RUSSIAN_WORDS = {
    # Глагольные формы
    "есмя", "есми", "есмь", "поидох", "поидохом", "приидох", "внидох",
    "бых", "быхом", "идох", "взях", "видех", "обретох", "рекох",
    "пошли", "пошел", "поехал", "поехали", "приехал", "приехали",
    "взяли", "взял", "застрелили", "застрелял", "поймали",
    # Наречия и частицы
    "велми", "вельми", "зело", "токмо", "паки", "понеже", "аще",
    "инде", "зане", "занеже", "убо", "яко", "ино",
    # Существительные
    "град", "грады", "земля", "земли", "перец", "мечь", "путь",
    # Местоимения и формы
    "язь", "азъ", "аз", "мя", "тя", "ся",
    # Титулы и имена
    "султан", "салтан", "хан", "бег", "бек", "возырь", "возыри",
    "мелик", "тучар", "шах", "ширваншах",
    # Географические
    "бедер", "бедери", "бедеря", "чюнер", "чюнере", "чюнеря",
    "гурмыз", "гурмыза", "гурмызе", "ормус", "ормуса",
    "дабыл", "дабыла", "дабыли", "келекот", "келекота",
    "гундустан", "гундустани", "гундустаньский",
    "хоросань", "хоросанский", "хоросанец", "хоросанцы",
    "бесермен", "бесермены", "бесерменский", "бесерменьский",
    "бесерменьской", "бесерменьская", "бесерменьское", "бесерменьские",
    "бесерменскую", "бесерменском", "бесерменским", "бесерменскими",
    # Древнерусские глагольные формы
    "бысть", "быти", "бяше", "бяху", "бяше", "рече", "рекоша",
    "сказа", "глагола", "глаголя", "глаголаше",
    "хожаше", "хождаше", "хожение", "хождение",
    "живяше", "стояше", "бояше",
    "идяше", "идяху", "имяше",
    # Дореволюционные формы с ъ/ь
    "бысть", "есть", "приидоша", "поидоша",
    "великомъ", "княжении", "тверскомъ",
    # Слова из текста
    "нет", "что", "это", "они", "его", "она", "тот",
    "все", "как", "был", "для", "или", "при",
    "фота", "алафу", "тенекь", "ковов", "тенек",
    "осподарыни", "осподарь",
    # Предлоги/союзы дореволюционной орфографии
    "въ", "изъ", "къ", "подъ", "надъ", "предъ", "объ",
}


def _is_cyr(s: str) -> bool:
    """Строка состоит только из кириллических букв."""
    return bool(_CYR.match(s))


def _morph_valid(word: str, morph) -> bool:
    """Проверяет, является ли слово валидным по pymorphy2."""
    if not morph:
        return False
    word_lower = word.lower()
    if word_lower in OLD_RUSSIAN_WORDS:
        return True
    try:
        parsed = morph.parse(word_lower)
        if not parsed:
            return False
        best = parsed[0]
        if best.score >= 0.01:
            return True
        if best.tag.POS is not None:
            return True
    except Exception:
        pass
    return False


def _is_dict_word(word: str, morph, _cache: dict = {}) -> bool:
    """Проверяет, находится ли слово в словаре pymorphy2 (DictionaryAnalyzer).
    
    Возвращает True только для слов, которые РЕАЛЬНО есть в словаре,
    а не угаданы через FakeDictionary / KnownSuffixAnalyzer и т.д.
    Также пробует вариант без концевого ъ (дореволюционная орфография).
    """
    if not morph:
        return False
    word_lower = word.lower()
    if word_lower in _cache:
        return _cache[word_lower]
    if word_lower in OLD_RUSSIAN_WORDS:
        _cache[word_lower] = True
        return True
    if word_lower and word_lower[0] in 'ьъ':
        _cache[word_lower] = False
        return False
    
    def _check_dict(w):
        try:
            parsed = morph.parse(w)
            if not parsed or not parsed[0].methods_stack:
                return False
            return type(parsed[0].methods_stack[0][0]).__name__ == "DictionaryAnalyzer"
        except Exception:
            return False
    
    result = _check_dict(word_lower)
    if not result and word_lower.endswith('ъ') and len(word_lower) > 2:
        result = _check_dict(word_lower[:-1])
    _cache[word_lower] = result
    return result


def _is_likely_spaced(tokens: list[str]) -> bool:
    """Проверяет, похожа ли последовательность на разорванное слово.
    
    Ослабленный порог: достаточно 30% коротких фрагментов (<=3 символа),
    или хотя бы 2 коротких фрагмента подряд.
    """
    if len(tokens) < 2:
        return False
    short = sum(1 for t in tokens if len(t) <= 3)
    if short / len(tokens) >= 0.3:
        return True
    # Есть ли хотя бы 2 коротких подряд?
    for k in range(len(tokens) - 1):
        if len(tokens[k]) <= 3 and len(tokens[k + 1]) <= 3:
            return True
    return False


def _morph_score(word: str, morph) -> float:
    """Возвращает морфологический score слова (0.0 если не распознано)."""
    if not morph:
        return 0.0
    word_lower = word.lower()
    if word_lower in OLD_RUSSIAN_WORDS:
        return 1.0
    try:
        parsed = morph.parse(word_lower)
        if parsed:
            return parsed[0].score
    except Exception:
        pass
    return 0.0


def _segment_tokens(tokens: list[str], morph) -> list[str]:
    """Группирует последовательность фрагментов в слова через DP.
    
    Ключевые идеи:
    - Двойная валидация: pymorphy2 (DictionaryAnalyzer) + navec (500K словарь)
    - Оба подтвердили + длинное слово → максимальный бонус
    - Только pymorphy2 подтвердил → средний бонус (может быть фрагмент)
    - Только navec подтвердил → хороший бонус (navec надёжнее для целых слов)
    - Разрез перед заглавной буквой дешевле (естественная граница)
    """
    import math
    
    n = len(tokens)
    if n == 0:
        return []
    if n == 1:
        return tokens[:]
    
    INF = float('-inf')
    dp = [(INF, -1)] * (n + 1)
    dp[0] = (0.0, -1)
    
    SPLIT_PENALTY = -2.0
    UPPERCASE_SPLIT_BONUS = 1.5
    
    for i in range(1, n + 1):
        for j in range(max(0, i - 15), i):
            if dp[j][0] == INF:
                continue
            
            word = "".join(tokens[j:i])
            word_lower = word.lower()
            char_len = len(word)
            
            is_dict = _is_dict_word(word, morph)
            in_navec = _in_navec(word)
            is_old_ru = word_lower in OLD_RUSSIAN_WORDS
            
            _SINGLE_CHAR_WORDS = {'в', 'с', 'к', 'о', 'у', 'я', 'а', 'и'}
            
            if char_len <= 1:
                if word_lower in _SINGLE_CHAR_WORDS:
                    morph_bonus = 1.5
                else:
                    morph_bonus = 0.0
            elif char_len <= 2:
                # 2-символьные: navec ненадёжен для таких коротких токенов.
                # Ограничиваем бонус — даже OLD_RUSSIAN_WORDS вроде "ся", "мя"
                # не должны получать 3.0, иначе "ся" перебьёт "отправился".
                if is_old_ru or (is_dict and in_navec):
                    morph_bonus = 1.5
                elif is_dict:
                    morph_bonus = 1.0
                else:
                    morph_bonus = 0.0
            elif is_old_ru:
                morph_bonus = 3.0
            elif is_dict and in_navec:
                # Оба источника подтверждают — надёжное слово
                if char_len >= 6:
                    morph_bonus = 3.0
                else:
                    morph_bonus = 2.0  # короткое — может быть фрагмент
            elif in_navec:
                # Navec подтвердил, pymorphy2 нет — скорее всего реальное слово
                if char_len >= 6:
                    morph_bonus = 2.5
                else:
                    morph_bonus = 1.5
            elif is_dict:
                # Только pymorphy2 (navec не подтвердил) — может быть ложное срабатывание
                if char_len >= 6:
                    morph_bonus = 1.2
                else:
                    morph_bonus = 0.8
            else:
                if morph:
                    parsed = morph.parse(word_lower)
                    raw = parsed[0].score if parsed else 0.0
                else:
                    raw = 0.0
                morph_bonus = raw * 0.1
            
            length_bonus = char_len * 0.3 + math.sqrt(char_len) * 0.3
            
            if j > 0:
                first_tok = tokens[j]
                if first_tok[0].isupper():
                    penalty = SPLIT_PENALTY + UPPERCASE_SPLIT_BONUS
                else:
                    penalty = SPLIT_PENALTY
            else:
                penalty = 0
            
            total = dp[j][0] + morph_bonus + length_bonus + penalty
            
            if total > dp[i][0]:
                dp[i] = (total, j)
    
    if dp[n][0] == INF:
        return [" ".join(tokens)]
    
    words = []
    pos = n
    while pos > 0:
        prev = dp[pos][1]
        words.append("".join(tokens[prev:pos]))
        pos = prev
    
    words.reverse()
    return words


def despace_line(line: str, morph) -> str:
    """Обрабатывает одну строку: склеивает разорванные слова."""
    if not line.strip():
        return line
    
    # Разбиваем строку на токены, сохраняя пунктуацию и пробелы
    parts = re.findall(r'[А-ЯЁа-яё]+|[^А-ЯЁа-яё]+', line)
    
    if len(parts) <= 1:
        return line
    
    result = []
    i = 0
    
    while i < len(parts):
        part = parts[i]
        
        if not _is_cyr(part):
            result.append(part)
            i += 1
            continue
        
        # Собираем последовательность: кир_токен, пробел, кир_токен, пробел, ...
        seq_tokens = [part]
        j = i + 1
        
        while j + 1 < len(parts):
            separator = parts[j]
            next_token = parts[j + 1]
            
            if not re.match(r'^\s+$', separator):
                break
            if not _is_cyr(next_token):
                break
            
            # Ломаем последовательность только при СИЛЬНОМ подтверждении:
            # оба слова >= 5 символов И подтверждены ОБОИМИ источниками,
            # И их объединение НЕ даёт валидного слова.
            last_tok = seq_tokens[-1]
            last_strong = (len(last_tok) >= 5
                           and _is_dict_word(last_tok, morph)
                           and _in_navec(last_tok))
            next_strong = (len(next_token) >= 5
                           and _is_dict_word(next_token, morph)
                           and _in_navec(next_token))
            if last_strong and next_strong:
                joined = last_tok + next_token
                if not (_in_navec(joined) or _is_dict_word(joined, morph)):
                    break
            
            if len(seq_tokens) >= 30:
                break
            
            seq_tokens.append(next_token)
            j += 2
        
        if len(seq_tokens) >= 2 and _is_likely_spaced(seq_tokens):
            # DP-сегментация: группируем токены в слова
            words = _segment_tokens(seq_tokens, morph)
            result.append(" ".join(words))
            i += len(seq_tokens) * 2 - 1
        else:
            result.append(part)
            i += 1
    
    return "".join(result)


def aggressive_despacing(text: str) -> str:
    """Агрессивная склейка разорванных пробелами слов.
    
    Обрабатывает каждую строку отдельно, склеивая последовательности
    коротких кириллических фрагментов в слова.
    """
    morph = None
    if MORPH_AVAILABLE:
        try:
            morph = MorphAnalyzer()
        except Exception:
            morph = None
    
    lines = text.split('\n')
    result_lines = []
    
    for line in lines:
        result_lines.append(despace_line(line, morph))
    
    return '\n'.join(result_lines)


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
        description="Агрессивная склейка разорванных пробелами слов (despacing). "
                    "Для OCR-текстов из старопечатных сканов."
    )
    ap.add_argument("--in", dest="inp", required=True, help="Входной TXT")
    ap.add_argument("--out", dest="out", required=True, help="Выходной TXT")
    ap.add_argument("--html", dest="html", help="Необязательный путь для HTML")
    ap.add_argument("--title", default="После десппейсинга", help="Заголовок HTML")
    args = ap.parse_args()

    src = Path(args.inp)
    dst = Path(args.out)
    text = src.read_text(encoding="utf-8", errors="replace")
    
    print(f"Входной текст: {len(text)} символов")
    despaced = aggressive_despacing(text)
    
    # Статистика: сколько пробелов убрали
    original_spaces = text.count(' ')
    result_spaces = despaced.count(' ')
    removed = original_spaces - result_spaces
    print(f"Убрано пробелов: {removed} ({original_spaces} -> {result_spaces})")
    
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(despaced, encoding="utf-8")
    if args.html:
        Path(args.html).write_text(to_html(despaced, args.title), encoding="utf-8")
    print(f"Сохранено: {dst}" + (f", {args.html}" if args.html else ""))


if __name__ == "__main__":
    main()
