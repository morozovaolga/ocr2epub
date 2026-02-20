import argparse
import inspect
import os
import re
from pathlib import Path
from html import escape as hesc

# Обходной путь для совместимости с Python 3.11+ (pymorphy2 использует устаревший inspect.getargspec)
if not hasattr(inspect, "getargspec"):
    def _getargspec(func):
        spec = inspect.getfullargspec(func)
        return spec.args, spec.varargs, spec.varkw, spec.defaults
    inspect.getargspec = _getargspec  # type: ignore[attr-defined]

# Попытка импортировать pymorphy2 для проверки валидности слов
try:
    from pymorphy2 import MorphAnalyzer
    MORPH_AVAILABLE = True
except ImportError:
    MORPH_AVAILABLE = False
    MorphAnalyzer = None
except Exception as e:
    MORPH_AVAILABLE = False
    MorphAnalyzer = None

# Navec — компактные русские эмбеддинги для дополнительной проверки слов
_navec_model = None

def _load_navec():
    """Ленивая загрузка navec модели."""
    global _navec_model
    if _navec_model is not None:
        return _navec_model
    try:
        from navec import Navec
        home = os.path.expanduser("~")
        model_path = os.path.join(home, ".navec", "navec_hudlit_v1_12B_500K_300d_100q.tar")
        if os.path.exists(model_path):
            _navec_model = Navec.load(model_path)
            return _navec_model
    except Exception:
        pass
    _navec_model = False
    return None

def _in_navec(word: str, _cache: dict = {}) -> bool:
    """Проверяет наличие слова в navec (500K слов)."""
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


LAT_TO_CYR = {
    # Существующие замены
    "A": "А", "a": "а",
    "B": "В", "E": "Е", "e": "е",
    "K": "К", "k": "к",
    "M": "М",
    "H": "Н",
    "O": "О", "o": "о",
    "P": "Р", "p": "р",
    "C": "С", "c": "с",
    "T": "Т",
    "X": "Х", "x": "х",
    "Y": "У", "y": "у",
    # Расширенные замены для смешанной латиницы/кириллицы
    "I": "И", "i": "и",
    "U": "У", "u": "у",
    "Z": "З", "z": "з",
    "D": "Д", "d": "д",
    "F": "Ф", "f": "ф",
    "G": "Г", "g": "г",
    "J": "Ж", "j": "ж",
    "L": "Л", "l": "л",
    "N": "Н", "n": "н",
    "R": "Р", "r": "р",
    "S": "С", "s": "с",
    "V": "В", "v": "в",
    "W": "Ш", "w": "ш",  # Редко, но возможно
}


# Древнерусская / архаичная лексика, не распознаваемая pymorphy2,
# но являющаяся валидными словами (используется для валидации при склейке)
OLD_RUSSIAN_WORDS = {
    # Глагольные формы
    "есмя", "есми", "есмь", "поидох", "поидохом", "приидох", "внидох",
    "бых", "быхом", "идох", "взях", "видех", "обретох", "рекох",
    # Наречия и частицы
    "велми", "вельми", "зело", "токмо", "паки", "понеже", "аще",
    "инде", "зане", "занеже", "убо", "яко", "ино",
    # Существительные
    "град", "грады",
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
    # Древнерусские глагольные формы
    "бысть", "быти", "бяше", "бяху", "рече", "рекоша",
    "сказа", "глагола", "глаголя", "глаголаше",
    "хожаше", "хождаше", "хожение", "хождение",
    "живяше", "стояше", "бояше", "идяше", "идяху", "имяше",
    # Дореволюционные формы с ъ/ь
    "приидоша", "поидоша",
    "великомъ", "княжении", "тверскомъ",
    # Предлоги/союзы дореволюционной орфографии
    "въ", "изъ", "къ", "подъ", "надъ", "предъ", "объ",
    # Слова из текста «Хождения за три моря»
    "фота", "алафу", "тенекь", "ковов", "тенек",
    "осподарыни", "осподарь",
}


def _is_old_russian_valid(word: str) -> bool:
    """Проверяет, является ли слово валидным древнерусским словом."""
    return word.lower() in OLD_RUSSIAN_WORDS


def replace_odd_symbols(text: str) -> str:
    repl = {
        "■": " ",
        "¬": "",
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "—", "―": "—",
        "“": "«", "”": "»", "„": "«", "‟": "»",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    return text


def convert_mixed_latin_to_cyr(text: str) -> str:
    token_re = re.compile(r"[A-Za-z\u0400-\u04FF]+(?:-[A-Za-z\u0400-\u04FF]+)*")
    def fix(m: re.Match) -> str:
        tok = m.group(0)
        has_cyr = re.search(r"[\u0400-\u04FF]", tok)
        has_lat = re.search(r"[A-Za-z]", tok)
        if not (has_cyr and has_lat):
            return tok
        return "".join(LAT_TO_CYR.get(ch, ch) for ch in tok)
    return token_re.sub(fix, text)


def join_spaced_letters(text: str, morph: MorphAnalyzer = None) -> str:
    """Склеивает последовательности коротких фрагментов через пробел.
    
    "В ы р е з к а" -> "Вырезка"
    "Ка ра м зи н" -> "Карамзин"
    
    Разрешены фрагменты до 3 символов (не только одиночные буквы).
    """
    def _join_if_safe(m: re.Match) -> str:
        spaced = m.group(0)
        parts = spaced.split()
        if len(parts) < 2:
            return spaced
        # Разрешаем части до 3 символов (не только одиночные буквы)
        if all(len(p) <= 3 and p.isalpha() for p in parts):
            joined = "".join(parts)
            if len(joined) < 3:
                return spaced
            # Проверяем словарь древнерусских слов (приоритет)
            if _is_old_russian_valid(joined):
                return joined
            # Проверяем navec (надёжнее pymorphy2 для целых слов)
            if _in_navec(joined):
                return joined
            if morph:
                parsed = morph.parse(joined.lower())
                score = parsed[0].score if parsed else 0.0
                # Двойная проверка: DictionaryAnalyzer + высокий score,
                # т.к. pymorphy2 даёт false-positive для мусорных склеек
                is_dict = False
                if parsed and parsed[0].methods_stack:
                    method = type(parsed[0].methods_stack[0][0]).__name__
                    is_dict = method == "DictionaryAnalyzer"
                if is_dict and score >= 0.3:
                    return joined
                # Если все одиночные буквы — склеиваем без колебаний
                if all(len(p) == 1 for p in parts):
                    return joined
                # Если все части <= 2 символа и score неплохой — склеиваем
                if all(len(p) <= 2 for p in parts) and score >= 0.01:
                    return joined
                return spaced
            # Без морфологии — склеиваем если все одиночные
            if all(len(p) == 1 for p in parts):
                return joined
            return spaced
        return spaced
    
    # Последовательности кириллических фрагментов <=3 символа через пробел
    return re.sub(
        r"(?<!\S)(?:[А-ЯЁа-яё]{1,3}\s){2,}[А-ЯЁа-яё]{1,3}(?!\S)",
        _join_if_safe, text
    )


_conf_cache: dict = {}
_pos_cache: dict = {}
_norm_cache: dict = {}


def _word_confidence(word: str, morph) -> float:
    """pymorphy2 confidence score; 0.0 для UNKN или если morph недоступен."""
    wl = word.lower()
    if wl in _conf_cache:
        return _conf_cache[wl]
    score = 0.0
    if morph:
        parses = morph.parse(wl)
        if parses:
            best = parses[0]
            if "UNKN" not in str(best.tag):
                score = best.score
    _conf_cache[wl] = score
    return score


def _get_pos(word: str, morph) -> str | None:
    """POS-тег лучшего разбора pymorphy2 (NOUN, VERB, PREP, CONJ…)."""
    wl = word.lower()
    if wl in _pos_cache:
        return _pos_cache[wl]
    pos = None
    if morph:
        parses = morph.parse(wl)
        if parses:
            best = parses[0]
            if "UNKN" not in str(best.tag):
                pos = best.tag.POS
    _pos_cache[wl] = pos
    return pos


def _get_norm(word: str, morph) -> str:
    """Нормальная форма (лемма) слова по pymorphy2."""
    wl = word.lower()
    if wl in _norm_cache:
        return _norm_cache[wl]
    norm = wl
    if morph:
        parses = morph.parse(wl)
        if parses:
            best = parses[0]
            if "UNKN" not in str(best.tag):
                norm = best.normal_form
    _norm_cache[wl] = norm
    return norm


_CYR_TOKEN_RE = re.compile(r'([А-Яа-яЁё]+|[^А-Яа-яЁё]+)')
_CYR_WORD_RE = re.compile(r'^[А-Яа-яЁё]+$')
_FUNCTIONAL_POS = frozenset({'CONJ', 'PRCL', 'NPRO'})


def merge_broken_words(text: str, morph=None, max_passes: int = 5) -> tuple:
    """Склеивает разорванные слова попарным обходом токенов.

    Алгоритм:
    - Токенизация текста на кириллические слова и разделители
    - Попарная проверка (left, пробел, right) → merged
    - merged ДОЛЖЕН быть в navec (500K словарь)
    - Защита предлогов/союзов/частиц/местоимений от ложных склеек
    - Многопроходность для каскадных склеек ("бе дный" → "бедный")

    Возвращает (очищенный_текст, количество_склеек).
    """
    total_merges = 0

    def _is_cyr(w):
        return bool(_CYR_WORD_RE.match(w))

    def _single_pass(txt):
        nonlocal total_merges
        tokens = _CYR_TOKEN_RE.findall(txt)
        result = []
        i = 0
        pass_merges = 0

        while i < len(tokens):
            if (i + 2 < len(tokens)
                    and _is_cyr(tokens[i])
                    and tokens[i + 1] == ' '
                    and _is_cyr(tokens[i + 2])):
                left = tokens[i]
                right = tokens[i + 2]
                merged = left + right
                ml = merged.lower()

                if _is_old_russian_valid(ml):
                    pass_merges += 1
                    result.append(merged)
                    i += 3
                    continue

                if _in_navec(ml):
                    if morph:
                        left_pos = _get_pos(left, morph)
                        right_conf = _word_confidence(right, morph)

                        if left_pos in _FUNCTIONAL_POS and right_conf > 0.3:
                            result.append(tokens[i])
                            i += 1
                            continue

                        if left_pos == 'PREP' and right_conf > 0.3:
                            merged_pos = _get_pos(merged, morph)
                            if merged_pos != 'ADVB':
                                right_norm = _get_norm(right, morph)
                                merged_norm = _get_norm(merged, morph)
                                if len(left) == 1:
                                    if merged_norm != left.lower() + right_norm:
                                        result.append(tokens[i])
                                        i += 1
                                        continue
                                else:
                                    if right_norm not in merged_norm:
                                        result.append(tokens[i])
                                        i += 1
                                        continue

                        left_conf = _word_confidence(left, morph)
                        merged_conf = _word_confidence(merged, morph)
                        right_conf = _word_confidence(right, morph)

                        do_merge = False
                        if left_conf == 0:
                            do_merge = True
                        elif merged_conf > left_conf:
                            do_merge = True
                        elif right_conf < 0.1:
                            do_merge = True

                        if do_merge:
                            pass_merges += 1
                            result.append(merged)
                            i += 3
                            continue
                    else:
                        pass_merges += 1
                        result.append(merged)
                        i += 3
                        continue

            result.append(tokens[i])
            i += 1

        total_merges += pass_merges
        return ''.join(result), pass_merges

    for _ in range(max_passes):
        text, count = _single_pass(text)
        if count == 0:
            break

    return text, total_merges




def fix_common_ocr_errors(text: str) -> str:
    # Fix common OCR errors: "па" -> "на", "то" -> "по", "са" -> "за" (in context), etc.
    # Be careful: only fix in specific contexts to avoid false positives
    fixes = [
        # "па" -> "на" (before any word starting with lowercase cyrillic)
        # This covers cases like "па дитя", "па столе", "па земле"
        (r"\bпа\s+([а-яё])", r"на \1"),
        
        # "то" -> "по" (расширенный контекст для пространственных предлогов)
        # Существительные в дательном падеже (куда/где)
        (r"\bто\s+(дороге|стене|полу|небу|земле|воде|берегу|берегам|берегу|берегам|"
         r"стороне|сторонам|сторонах|окну|окнам|окнах|двери|дверям|дверях|"
         r"крыше|крышам|крышах|крыше|крышам|крышах|лесу|лесам|лесах|"
         r"полю|полям|полях|морю|морям|морях|реке|рекам|реках|"
         r"горе|горам|горах|дому|домам|домах|городу|городам|городах)\b", r"по \1"),
        
        # "то" -> "по" (в контексте наречий и выражений)
        (r"\bто\s+(мере|степени|крайней|меньшей|большей|"
         r"причине|причинам|поводу|поводам|случаю|случаям|"
         r"примеру|примерам|образцу|образцам|мнению|мнениям|"
         r"сравнению|сравнениям|отношению|отношениям)\b", r"по \1"),
        
        # "са" -> "за" (в контексте существительных в творительном падеже)
        (r"\bса\s+(столом|домом|дверью|окном|забором|заборами|"
         r"стеной|стенами|крышей|крышами|деревом|деревьями|"
         r"кустом|кустами|камнем|камнями|углом|углами|"
         r"спиной|спинами|рукой|руками|ногой|ногами|головой|головами)\b", r"за \1"),
    ]
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def remove_page_numbers_and_headers(text: str) -> str:
    """Удаляет номера страниц и колонтитулы из текста.
    
    Паттерны:
    - Одиночные числа на отдельных строках (номера страниц)
    - Колонтитулы типа "Т. II. К. 8."
    - Повторяющиеся заголовки с номерами вида "172 ПУТЕШЕСТВИЕ"
    - Колонтитулы "ТВЕРСКАГО КУПЦА АФАНАСИЯ НИКИТИНА В ИНДИЮ 173"
    """
    # Одиночные числа на отдельных строках (номера страниц)
    text = re.sub(r'\n\s*\d{1,3}\s*\n', '\n', text)
    
    # Числа в начале или конце строки, окружённые пустыми строками
    text = re.sub(r'\n\n\s*\d{1,3}\s*\n\n', '\n\n', text)
    
    # Колонтитулы типа "Т. II. К. 8." или "Т. II. К. 8. *"
    text = re.sub(r'Т\.\s*II\.\s*К\.\s*\d+\.?\s*\*?\s*', '', text)
    
    # Вставки типа "172 ПУТЕШЕСТВИЕ" (число + слово капсом в начале строки)
    text = re.sub(r'^\d{1,3}\s+[А-ЯЁ]{4,}', '', text, flags=re.MULTILINE)
    
    # Колонтитулы "ТВЕРСКАГО КУПЦА АФАНАСИЯ НИКИТИНА В ИНДИЮ 173"
    text = re.sub(
        r'^[А-ЯЁ\s]{15,}\d{1,3}\s*$',
        '', text, flags=re.MULTILINE
    )
    
    # Дублированные заголовки капсом на отдельных строках (>15 символов капсом)
    text = re.sub(
        r'^\s*[А-ЯЁ\s,]{15,}\s*$',
        '', text, flags=re.MULTILINE
    )
    
    # Убираем образовавшиеся множественные пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text


def cleanup_text(text: str, preserve_newlines: bool = False) -> str:
    morph = None
    if MORPH_AVAILABLE:
        try:
            morph = MorphAnalyzer()
        except Exception:
            morph = None
    
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\u200B\u200C\u200D\u00AD]", "", t)
    t = replace_odd_symbols(t)
    t = t.replace("–", "—")
    t = re.sub(r"\s*—\s*", " — ", t)
    if not preserve_newlines:
        t = re.sub(r"([\wА-Яа-яЁё])[-\-\–\—]\n(?=[\wА-Яа-яЁё])", r"\1", t)
        t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = remove_page_numbers_and_headers(t)
    t = join_spaced_letters(t, morph)
    t, merge_count = merge_broken_words(t, morph)
    t = fix_common_ocr_errors(t)
    t = convert_mixed_latin_to_cyr(t)
    return t.strip()


def to_html(text: str, title: str) -> str:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "\n".join(f"<p>{hesc(p)}</p>" for p in paras)
    return (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        f"<title>{hesc(title)}</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        "<style>body{font:18px/1.6 Georgia,Times,\"Times New Roman\",serif;margin:2rem;max-width:48rem;color:#111;background:#fff} p{margin:0 0 1rem}</style>\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def main():
    ap = argparse.ArgumentParser(description="Post-cleanup: join spaced letters, fix intraword gaps, Latin→Cyr mix. Saves TXT/HTML.")
    ap.add_argument("--in", dest="inp", required=True, help="Входной TXT")
    ap.add_argument("--out", dest="out", required=True, help="Выходной TXT")
    ap.add_argument("--html", dest="html", help="Необязательный путь для HTML")
    ap.add_argument("--title", default="После доп. очистки", help="Заголовок HTML")
    ap.add_argument("--preserve-newlines", action="store_true",
                    help="Не сливать одиночные переносы строк (для стихов)")
    args = ap.parse_args()

    src = Path(args.inp)
    dst = Path(args.out)
    text = src.read_text(encoding="utf-8", errors="replace")
    cleaned = cleanup_text(text, preserve_newlines=args.preserve_newlines)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(cleaned, encoding="utf-8")
    if args.html:
        Path(args.html).write_text(to_html(cleaned, args.title), encoding="utf-8")
    print(f"Saved: {dst}" + (f", {args.html}" if args.html else ""))


if __name__ == "__main__":
    main()
