import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import fitz  # PyMuPDF
from natasha import (
    Doc,
    MorphVocab,
    NewsEmbedding,
    NewsMorphTagger,
    NewsNERTagger,
    Segmenter,
)


@dataclass(frozen=True)
class Mention:
    text: str
    normal: str
    type: str


@dataclass(frozen=True)
class SpanMention(Mention):
    start: int
    stop: int


def load_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    pages = [page.get_text("text") for page in doc]
    return "\n".join(pages)


class NatashaPipeline:
    def __init__(self):
        self.segmenter = Segmenter()
        self.embedding = NewsEmbedding()
        self.morph_tagger = NewsMorphTagger(self.embedding)
        self.ner_tagger = NewsNERTagger(self.embedding)
        self.morph_vocab = MorphVocab()

    def extract(self, text: str, allowed_types: Sequence[str]) -> List[Mention]:
        spans = self.extract_spans(text, allowed_types)
        return [Mention(text=s.text, normal=s.normal, type=s.type) for s in spans]

    def extract_spans(self, text: str, allowed_types: Sequence[str]) -> List[SpanMention]:
        doc = Doc(text)
        doc.segment(self.segmenter)
        doc.tag_morph(self.morph_tagger)
        doc.tag_ner(self.ner_tagger)
        mentions: list[SpanMention] = []
        for span in doc.spans:
            if span.type not in allowed_types:
                continue
            try:
                span.normalize(self.morph_vocab)
            except ValueError:
                pass
            normal = span.normal or span.text
            mentions.append(
                SpanMention(
                    text=span.text,
                    normal=normal,
                    type=span.type,
                    start=span.start,
                    stop=span.stop,
                )
            )
        return mentions


def parse_types(types: str) -> List[str]:
    return [tok.strip().upper() for tok in types.split(",") if tok.strip()]


def collect_mentions(text: str, allowed_types: Sequence[str], deduplicate: bool = True) -> List[Mention]:
    pipeline = NatashaPipeline()
    mentions = pipeline.extract(text, allowed_types)
    if deduplicate:
        return dedupe(mentions)
    return mentions


def collect_span_mentions(
    text: str,
    allowed_types: Sequence[str],
    deduplicate: bool = False,
) -> List[SpanMention]:
    pipeline = NatashaPipeline()
    spans = pipeline.extract_spans(text, allowed_types)
    if not deduplicate:
        return spans
    # Дедупликация с сохранением первого порядка появления.
    seen = set()
    unique: list[SpanMention] = []
    for m in spans:
        key = (m.normal, m.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(m)
    return unique


def dedupe(mentions: Iterable[Mention]) -> List[Mention]:
    seen = set()
    unique = []
    for mention in mentions:
        key = (mention.normal, mention.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)
    return unique


def build_summary(
    pdf_mentions: Sequence[Mention],
    clean_mentions: Sequence[Mention],
) -> Tuple[List[Tuple[str, Mention]], List[Tuple[str, Mention]]]:
    pdf_map = {(m.normal, m.type): m for m in pdf_mentions}
    clean_map = {(m.normal, m.type): m for m in clean_mentions}
    pdf_keys = set(pdf_map)
    clean_keys = set(clean_map)
    missing_in_clean = sorted(pdf_keys - clean_keys, key=lambda k: (k[1], k[0]))
    missing_in_pdf = sorted(clean_keys - pdf_keys, key=lambda k: (k[1], k[0]))
    pdf_missing = [(key[1], pdf_map[key]) for key in missing_in_clean]
    clean_missing = [(key[1], clean_map[key]) for key in missing_in_pdf]
    return pdf_missing, clean_missing


def format_report(
    pdf_missing: Sequence[Tuple[str, Mention]],
    clean_missing: Sequence[Tuple[str, Mention]],
) -> str:
    lines = []
    if pdf_missing:
        lines.append("Из PDF (современное написание) оказались только в PDF:")
        for kind, mention in pdf_missing:
            lines.append(f"- {kind}: {mention.normal} ({mention.text})")
    else:
        lines.append("Из PDF ничего не исчезло в final_clean.txt.")
    lines.append("")
    if clean_missing:
        lines.append("Из final_clean.txt появились новые сущности:")
        for kind, mention in clean_missing:
            lines.append(f"- {kind}: {mention.normal} ({mention.text})")
    else:
        lines.append("Из final_clean.txt ничего не добавлено по сравнению с PDF.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Сравнивает именованные сущности PDF и final_clean.txt через Natasha.")
    parser.add_argument("--pdf", required=True, help="PDF с современным текстом (или близким к нему)")
    parser.add_argument("--clean", required=True, help="final_clean.txt")
    parser.add_argument("--out", default="natasha_diff.txt", help="Файл с отчётом")
    parser.add_argument("--types", default="PER,LOC", help="Типы сущностей для сравнения (PER, LOC, ORG)")
    parser.add_argument("--keep-order", action="store_true", help="Сохранять первый порядок появления в тексте")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    clean_path = Path(args.clean)
    out_path = Path(args.out)

    for path in (pdf_path, clean_path):
        if not path.exists():
            parser.error(f"Файл не найден: {path}")

    allowed = parse_types(args.types)

    pdf_text = load_pdf_text(pdf_path)
    clean_text = clean_path.read_text(encoding="utf-8", errors="ignore")

    pdf_mentions = collect_mentions(pdf_text, allowed, deduplicate=not args.keep_order)
    clean_mentions = collect_mentions(clean_text, allowed, deduplicate=not args.keep_order)

    pdf_missing, clean_missing = build_summary(pdf_mentions, clean_mentions)
    report = format_report(pdf_missing, clean_missing)
    out_path.write_text(report, encoding="utf-8")
    print(f"Сравнение готово, {len(pdf_missing)} сущностей потеряно, {len(clean_missing)} добавлено. Сохранил: {out_path}")


if __name__ == "__main__":
    main()

