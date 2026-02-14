import argparse
import inspect
import re
from pathlib import Path
from typing import Iterable

if not hasattr(inspect, "getargspec"):
    def _getargspec(func):
        spec = inspect.getfullargspec(func)
        return spec.args, spec.varargs, spec.varkw, spec.defaults
    inspect.getargspec = _getargspec  # type: ignore[attr-defined]

try:
    from pymorphy2 import MorphAnalyzer
    MORPH_AVAILABLE = True
except ImportError:
    print("Предупреждение: pymorphy2 не установлен. Контекстная проверка недоступна.")
    print("Установите: pip install pymorphy2")
    MorphAnalyzer = None
    MORPH_AVAILABLE = False

WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)
DEFAULT_PRONOUNS = {"я", "ты", "он", "она", "оно", "мы", "вы", "они"}


def iter_words(text: str) -> Iterable[str]:
    return WORD_RE.findall(text)


def is_matching_pronoun(word: str, morph: MorphAnalyzer, pronouns: set[str]) -> bool:
    word_norm = word.lower()
    if word_norm in pronouns:
        return True
    for parse in morph.parse(word):
        if parse.tag.POS == "NPRO" and parse.normal_form in pronouns:
            return True
    return False


def has_verb_form(word: str, morph: MorphAnalyzer) -> bool:
    for parse in morph.parse(word):
        if parse.tag.POS in {"VERB", "INFN"}:
            return True
    return False


def analyze_text(text: str, pronouns: set[str], morph: MorphAnalyzer) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    warnings = []
    for sentence in sentences:
        tokens = list(iter_words(sentence))
        for idx in range(len(tokens) - 1):
            prev_word = tokens[idx]
            curr_word = tokens[idx + 1]
            if is_matching_pronoun(prev_word, morph, pronouns) and not has_verb_form(curr_word, morph):
                window_start = max(0, idx - 1)
                window_end = min(len(tokens), idx + 3)
                snippet = " ".join(tokens[window_start:window_end])
                warnings.append(
                    f"Пара {prev_word} + {curr_word} в предложении «{sentence.strip()}» ({snippet}) выглядит неправильно: {curr_word} не распознан как глагол."
                )
        warnings.extend(check_split_words(tokens, morph, sentence.strip()))
    return warnings


def check_split_words(tokens: list[str], morph: MorphAnalyzer, sentence: str) -> list[str]:
    """
    Проверяет, не являются ли два соседних слова разорванным словом.
    НЕ предлагает склеивать, если второе слово - союз, предлог, частица или местоимение.
    """
    warnings: list[str] = []
    
    # Слова, которые НЕ должны склеиваться с предыдущим словом
    # (союзы, предлоги, частицы, местоимения)
    standalone_parts_of_speech = {
        "CONJ",  # Союз
        "PREP",  # Предлог
        "PRCL",  # Частица
        "NPRO",  # Местоимение
        "INTJ",  # Междометие
    }
    
    for idx in range(len(tokens) - 1):
        first, second = tokens[idx], tokens[idx + 1]
        
        # Пропускаем слишком короткие комбинации
        if len(first) + len(second) < 5:
            continue
        
        # Проверяем, является ли второе слово союзом, предлогом и т.д.
        second_parses = morph.parse(second)
        second_is_standalone = any(
            p.tag.POS in standalone_parts_of_speech 
            for p in second_parses
        )
        
        # Если второе слово - союз/предлог/частица, НЕ предлагаем склеивать
        if second_is_standalone:
            continue
        
        # Проверяем, является ли первое слово союзом/предлогом (тоже не склеиваем)
        first_parses = morph.parse(first)
        first_is_standalone = any(
            p.tag.POS in standalone_parts_of_speech 
            for p in first_parses
        )
        if first_is_standalone:
            continue
        
        # Проверяем комбинацию только если оба слова - существительные, прилагательные или глаголы
        first_is_content_word = any(
            p.tag.POS in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS", "VERB", "INFN"}
            for p in first_parses
        )
        second_is_content_word = any(
            p.tag.POS in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS", "VERB", "INFN"}
            for p in second_parses
        )
        
        # Если хотя бы одно слово не является знаменательным, не проверяем склейку
        if not (first_is_content_word and second_is_content_word):
            continue
        
        combined = first + second
        combined = re.sub(r"[^А-Яа-яёЁ]", "", combined)
        if not combined:
            continue
        
        combined_parses = [p for p in morph.parse(combined) if p.tag.POS in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS", "VERB"}]
        if not combined_parses:
            continue
        
        # Проверяем, что склеенное слово действительно отличается от исходных
        single_parses = morph.parse(first)
        combined_norm = combined_parses[0].normal_form
        if any(p.normal_form == combined_norm for p in single_parses):
            continue
        if any(p.normal_form == combined_norm for p in second_parses):
            continue
        
        # Дополнительная проверка: если оба слова валидны по отдельности и часто встречаются вместе,
        # скорее всего это правильное сочетание, а не ошибка OCR
        first_is_valid = any(p.tag.POS in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS", "VERB", "INFN"} for p in first_parses)
        second_is_valid = any(p.tag.POS in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS", "VERB", "INFN"} for p in second_parses)
        
        # Если оба слова валидны и могут быть частью правильной фразы, пропускаем
        if first_is_valid and second_is_valid:
            # Проверяем, не является ли это типичным сочетанием (прилагательное + существительное и т.д.)
            first_has_adj = any(p.tag.POS in {"ADJF", "ADJS"} for p in first_parses)
            second_has_noun = any(p.tag.POS == "NOUN" for p in second_parses)
            if first_has_adj and second_has_noun:
                continue  # Это нормальное сочетание "прилагательное + существительное"
        
        snippet = " ".join(tokens[max(0, idx - 1): min(len(tokens), idx + 3)])
        warnings.append(
            f"Похоже, что «{first} {second}» в «{snippet}» должно быть «{combined_parses[0].word}» (норма: {combined_norm}). Рассмотрите склейку."
        )
    return warnings


def main():
    parser = argparse.ArgumentParser(
        description="Контекстная проверка: ищет конструкции «местоимение + глагол» с неправильно распознанной формой."
    )
    parser.add_argument("--in", dest="inp", required=True, help="final_clean.txt для проверки контекста")
    parser.add_argument("--out", default="context_warnings.txt", help="Куда сохранять предупреждения")
    parser.add_argument(
        "--pronouns",
        default=",".join(sorted(DEFAULT_PRONOUNS)),
        help="Через запятую разделённый список местоимений (по умолчанию: %(default)s)",
    )
    args = parser.parse_args()

    pronouns = set(tok.strip().lower() for tok in args.pronouns.split(",") if tok.strip())

    if not MORPH_AVAILABLE:
        print("Ошибка: pymorphy2 не установлен. Установите: pip install pymorphy2")
        return 1
    
    text = Path(args.inp).read_text(encoding="utf-8", errors="ignore")
    morph = MorphAnalyzer()

    warnings = analyze_text(text, pronouns, morph)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if warnings:
        out_path.write_text("\n".join(warnings), encoding="utf-8")
        print(f"Найдено {len(warnings)} потенциальных контекстных ошибок, сохранено в {out_path}")
    else:
        out_path.write_text("Ошибок не найдено.\n", encoding="utf-8")
        print(f"Ошибок не найдено, создал пустой отчёт {out_path}")


if __name__ == "__main__":
    main()

