import argparse
import difflib
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from natasha_entity_check import (
    SpanMention,
    collect_span_mentions,
    load_pdf_text,
    parse_types,
)

_WS_RE = re.compile(r"\s+")

# Частые латинские символы, которые OCR путает с кириллицей
_LAT_TO_CYR = str.maketrans(
    {
        "A": "А", "a": "а",
        "B": "В",
        "C": "С", "c": "с",
        "E": "Е", "e": "е",
        "H": "Н",
        "K": "К", "k": "к",
        "M": "М",
        "O": "О", "o": "о",
        "P": "Р", "p": "р",
        "T": "Т",
        "X": "Х", "x": "х",
        "Y": "У", "y": "у",
    }
)


def _norm_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_LAT_TO_CYR)
    s = s.replace("Ё", "Е").replace("ё", "е")
    s = _WS_RE.sub(" ", s.strip())
    return s.lower()


def _similarity(a: str, b: str) -> float:
    na = _norm_for_match(a)
    nb = _norm_for_match(b)
    if not na or not nb:
        return 0.0
    base = difflib.SequenceMatcher(a=na, b=nb).ratio()
    # Лёгкий бонус за совпадение количества “слов” (часто совпадает падеж/форма)
    if len(na.split()) == len(nb.split()):
        base += 0.03
    return min(base, 1.0)


def _best_pdf_form(clean_text: str, pdf_forms: list[str], min_similarity: float) -> str | None:
    if not pdf_forms:
        return None
    best = None
    best_score = -1.0
    for cand in pdf_forms:
        sc = _similarity(clean_text, cand)
        if sc > best_score:
            best_score = sc
            best = cand
    if best is None or best_score < min_similarity:
        return None
    return best


def _select_non_overlapping(
    repls: list[tuple[int, int, str, str, str]],
) -> list[tuple[int, int, str, str, str]]:
    """Выбираем непересекающиеся замены, предпочитая более длинные спаны."""
    # repl = (start, stop, old, new, kind)
    ordered = sorted(repls, key=lambda r: (-(r[1] - r[0]), r[0]))
    chosen: list[tuple[int, int, str, str, str]] = []
    intervals: list[tuple[int, int]] = []

    def overlaps(a0: int, a1: int) -> bool:
        for b0, b1 in intervals:
            if a0 < b1 and b0 < a1:
                return True
        return False

    for r in ordered:
        s, e = r[0], r[1]
        if s < 0 or e <= s:
            continue
        if overlaps(s, e):
            continue
        chosen.append(r)
        intervals.append((s, e))

    # Для применения замен в тексте — от конца к началу
    return sorted(chosen, key=lambda r: r[0], reverse=True)


def format_sync_report(applied: list[tuple[str, str, str, int]]) -> str:
    if not applied:
        return "Замены не применялись: текст уже совпадает с PDF."
    lines = ["Применённые замены (читальный текст → PDF):"]
    for kind, old, new, count in applied:
        lines.append(f"- {kind}: {old} → {new} (заменено {count} раз)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Гармонизирует final_clean.txt с упоминаниями из PDF (точечно, по спанам Natasha)."
    )
    parser.add_argument("--pdf", required=True, help="PDF с эталонными сущностями")
    parser.add_argument("--clean", required=True, help="final_clean.txt для правки")
    parser.add_argument(
        "--out",
        help="Если указан, сохраняет результат в отдельный файл; иначе перезаписывает `clean`",
    )
    parser.add_argument("--report", help="Файл для отчёта по заменам")
    parser.add_argument("--types", default="PER,LOC", help="Типы сущностей (PER, LOC, ORG)")
    parser.add_argument("--keep-order", action="store_true", help="(устар.) Не удалять дубликаты сущностей")
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.78,
        help="Минимальная похожесть (0..1), чтобы применить замену формы (по умолчанию: 0.78)",
    )
    parser.add_argument(
        "--max-pdf-forms",
        type=int,
        default=8,
        help="Сколько наиболее частых форм сущности из PDF хранить как кандидаты (по умолчанию: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не писать файл, только вывести/сохранить отчёт",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    clean_path = Path(args.clean)

    for path in (pdf_path, clean_path):
        if not path.exists():
            parser.error(f"Файл не найден: {path}")

    allowed = parse_types(args.types)

    pdf_text = load_pdf_text(pdf_path)
    clean_text = clean_path.read_text(encoding="utf-8", errors="ignore")

    # 1) Собираем формы сущностей из PDF (с частотами)
    pdf_spans: list[SpanMention] = collect_span_mentions(pdf_text, allowed, deduplicate=False)
    pdf_forms_counter: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for m in pdf_spans:
        key = (m.normal, m.type)
        t = (m.text or "").strip()
        if t:
            pdf_forms_counter[key][t] += 1

    pdf_forms: dict[tuple[str, str], list[str]] = {}
    for key, cnt in pdf_forms_counter.items():
        pdf_forms[key] = [t for t, _ in cnt.most_common(max(1, int(args.max_pdf_forms)))]

    # 2) Ищем сущности в clean и готовим точечные замены по позициям
    clean_spans: list[SpanMention] = collect_span_mentions(clean_text, allowed, deduplicate=False)
    candidates: list[tuple[int, int, str, str, str]] = []
    for m in clean_spans:
        key = (m.normal, m.type)
        forms = pdf_forms.get(key)
        if not forms:
            continue
        old = (m.text or "")
        if not old.strip():
            continue
        best = _best_pdf_form(old, forms, min_similarity=float(args.min_similarity))
        if not best:
            continue
        if old == best:
            continue
        candidates.append((m.start, m.stop, old, best, m.type))

    chosen = _select_non_overlapping(candidates)

    # 3) Применяем замены от конца к началу
    new_text = clean_text
    applied_counter: Counter[tuple[str, str, str]] = Counter()
    for start, stop, old, new, kind in chosen:
        current = new_text[start:stop]
        if current != old:
            continue
        new_text = new_text[:start] + new + new_text[stop:]
        applied_counter[(kind, old, new)] += 1

    applied = [
        (kind, old, new, count)
        for (kind, old, new), count in applied_counter.most_common()
    ]

    target_path = Path(args.out) if args.out else clean_path
    if not args.dry_run:
        target_path.write_text(new_text, encoding="utf-8")

    report = format_sync_report(applied)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    else:
        print(report)

    if args.dry_run:
        print("Dry-run: файл не записывался.")
    else:
        print(f"Гармонизация выполнена, сохранил: {target_path}")


if __name__ == "__main__":
    main()

