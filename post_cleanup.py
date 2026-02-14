import argparse
import inspect
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
    # Если есть другие ошибки (например, проблемы с совместимостью), просто отключаем
    MORPH_AVAILABLE = False
    MorphAnalyzer = None


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
    # "В ы р е з к а" -> "Вырезка"
    # Очень консервативная склейка: только если все части - одиночные буквы
    def _join_if_safe(m: re.Match) -> str:
        spaced = m.group(0)
        parts = spaced.split()
        # Склеиваем только если ВСЕ части - одиночные буквы (не слова)
        if all(len(p) == 1 and p.isalpha() for p in parts):
            joined = spaced.replace(" ", "")
            # Если доступен морфологический анализатор, проверяем валидность
            if morph:
                parsed = morph.parse(joined)
                # Если склеенное слово не валидно, оставляем как есть
                if not parsed or parsed[0].score < 0.1:
                    return spaced
            return joined
        return spaced
    
    return re.sub(r"(?<!\S)(?:[А-ЯЁа-яё]\s){2,}[А-ЯЁа-яё](?!\S)", _join_if_safe, text)


def fix_intraword_small_gaps(text: str, morph: MorphAnalyzer = None) -> str:
    # Join cases like "обни мают" when parts are mostly short
    # ОЧЕНЬ консервативная склейка: только если части явно не являются валидными словами
    def _sub(m: re.Match) -> str:
        seg = m.group(0)
        parts = seg.split()
        
        # Если доступен морфологический анализатор, проверяем валидность частей
        if morph:
            # Проверяем, являются ли части валидными словами
            parts_are_valid_words = []
            for part in parts:
                if len(part) > 2:  # Проверяем только слова длиннее 2 символов
                    parsed = morph.parse(part)
                    # Считаем слово валидным, если score высокий (≥0.5) ИЛИ это известная часть речи
                    is_valid = parsed and (
                        parsed[0].score >= 0.5 or  # Высокая уверенность
                        any(p.tag.POS in {
                            "NOUN", "ADJF", "ADJS", "VERB", "INFN", "PRTF", "PRTS",
                            "NPRO", "PREP", "CONJ", "PRCL", "INTJ"  # Добавляем местоимения, предлоги и др.
                        } for p in parsed[:2])  # Проверяем первые 2 варианта
                    )
                    parts_are_valid_words.append(is_valid)
                else:
                    parts_are_valid_words.append(False)  # Короткие части не проверяем
            
            # Если хотя бы одна часть - валидное слово, НЕ склеиваем
            if any(parts_are_valid_words):
                return seg
        
        # Старая логика: склеиваем только если большинство частей очень короткие
        short = sum(1 for p in parts if len(p) <= 2)
        # Более строгие условия: все части должны быть короткими (≤2) И общая длина ≥5
        if len(parts) >= 2 and short == len(parts) and len("".join(parts)) >= 5:
            return "".join(parts)
        return seg
    
    return re.sub(r"(?<![\w-])(?:[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)+)(?![\w-])", _sub, text)




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


def cleanup_text(text: str) -> str:
    # Инициализируем морфологический анализатор, если доступен
    morph = None
    if MORPH_AVAILABLE:
        try:
            morph = MorphAnalyzer()
        except Exception:
            morph = None
    
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\u200B\u200C\u200D\u00AD]", "", t)
    t = replace_odd_symbols(t)
    # Em dash spacing: unify and ensure spaces on both sides
    t = t.replace("–", "—")
    t = re.sub(r"\s*—\s*", " — ", t)
    # Dehyphenate across newlines (already normalized, but keep it safe)
    t = re.sub(r"([\wА-Яа-яЁё])[-\-\–\—]\n(?=[\wА-Яа-яЁё])", r"\1", t)
    # Merge single line breaks inside paragraphs
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)
    # Collapse spaces
    t = re.sub(r"[ \t]{2,}", " ", t)
    # Консервативная склейка с проверкой валидности слов
    t = join_spaced_letters(t, morph)
    t = fix_intraword_small_gaps(t, morph)
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
    args = ap.parse_args()

    src = Path(args.inp)
    dst = Path(args.out)
    text = src.read_text(encoding="utf-8", errors="replace")
    cleaned = cleanup_text(text)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(cleaned, encoding="utf-8")
    if args.html:
        Path(args.html).write_text(to_html(cleaned, args.title), encoding="utf-8")
    print(f"Saved: {dst}" + (f", {args.html}" if args.html else ""))


if __name__ == "__main__":
    main()
