import argparse
import itertools
import json
import re
from pathlib import Path

import fitz  # PyMuPDF


def _collect_lines(block) -> list[tuple[str, float]]:
    """Извлекает строки из блока PyMuPDF как список (текст, x0)."""
    result = []
    for ln in block.get("lines", []):
        spans = ln.get("spans", [])
        txt = "".join(sp.get("text", "") for sp in spans)
        x0 = ln.get("bbox", [0])[0]
        result.append((txt, x0))
    return result


def collect_block_text(block) -> str:
    pairs = _collect_lines(block)
    text = "\n".join(txt for txt, _ in pairs)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)[\-‑–—]\n(?=\w)", r"\1", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


_JUNK_LINE_RE = re.compile(r"^[\s\-–—&*•·#§†‡©®™°¬]+$")


def _is_junk_line(text: str) -> bool:
    """Строка-мусор: только спецсимволы, без букв и цифр."""
    if not text:
        return True
    if _JUNK_LINE_RE.fullmatch(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Footnote detection
# ---------------------------------------------------------------------------

def _is_footnote_body_block(raw_block, page_height: float, med_size: float) -> bool:
    """Блок-тело сноски: внизу страницы, мелкий шрифт, начинается с цифры."""
    bbox = raw_block.get("bbox", [0, 0, 0, 0])
    if bbox[1] < page_height * 0.65:
        return False
    lines = raw_block.get("lines", [])
    total_chars = 0
    wsum = 0.0
    for ln in lines:
        for sp in ln.get("spans", []):
            s = sp.get("size", 0)
            t = sp.get("text", "") or ""
            n = len(t)
            if n and s:
                wsum += s * n
                total_chars += n
    if total_chars == 0:
        return False
    avg_size = wsum / total_chars
    if avg_size >= med_size * 0.92:
        return False
    full = "".join(
        sp.get("text", "")
        for ln in lines for sp in ln.get("spans", [])
    ).strip()
    if not full or not re.match(r"^\d", full):
        return False
    return True


def _parse_footnote_block(raw_block) -> list[tuple[str, str]]:
    """Разбирает блок сносок на пары (маркер, текст)."""
    full_text = ""
    for ln in raw_block.get("lines", []):
        full_text += "".join(sp.get("text", "") for sp in ln.get("spans", [])) + "\n"
    footnotes = []
    cur_marker = None
    cur_lines: list[str] = []
    for line in full_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s*[.)]*\s+(.*)", line)
        if m:
            if cur_marker is not None:
                footnotes.append((cur_marker, " ".join(cur_lines).strip()))
            cur_marker = m.group(1)
            cur_lines = [m.group(2)] if m.group(2) else []
        elif cur_marker is not None:
            cur_lines.append(line)
    if cur_marker is not None:
        footnotes.append((cur_marker, " ".join(cur_lines).strip()))
    return footnotes


def _is_superscript_digit(span, block_med_size: float = 0) -> bool:
    """Superscript-цифра (маркер сноски в основном тексте).

    Проверяет PyMuPDF superscript-флаг (bit 0) и, как фоллбэк,
    сильно уменьшенный размер шрифта относительно медианы блока.
    """
    text = span.get("text", "").strip()
    if not text or not re.fullmatch(r"\d{1,3}", text):
        return False
    if span.get("flags", 0) & 1:
        return True
    sz = span.get("size", 0)
    if block_med_size > 0 and sz > 0 and sz < block_med_size * 0.85:
        return True
    return False


def _block_median_size(block) -> float:
    """Медианный размер шрифта блока (взвешенный по символам)."""
    sizes: list[float] = []
    for ln in block.get("lines", []):
        for sp in ln.get("spans", []):
            s = sp.get("size", 0)
            n = len(sp.get("text", ""))
            if s and n:
                sizes.extend([s] * n)
    if not sizes:
        return 0
    sizes.sort()
    return sizes[len(sizes) // 2]


def collect_block_text_fn(block, fn_id_map: dict[str, int]) -> str:
    """collect_block_text с заменой superscript-маркеров на {{fn:N}}."""
    med = _block_median_size(block)
    line_texts = []
    for ln in block.get("lines", []):
        parts = []
        for sp in ln.get("spans", []):
            text = sp.get("text", "")
            marker = text.strip()
            if _is_superscript_digit(sp, med) and marker in fn_id_map:
                parts.append(f"{{{{fn:{fn_id_map[marker]}}}}}")
            else:
                parts.append(text)
        line_texts.append("".join(parts))
    text = "\n".join(line_texts)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)[\-‑–—]\n(?=\w)", r"\1", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def collect_block_text_raw_fn(block, fn_id_map: dict[str, int]) -> str:
    """collect_block_text_raw с заменой superscript-маркеров на {{fn:N}}."""
    med = _block_median_size(block)
    line_texts = []
    for ln in block.get("lines", []):
        parts = []
        for sp in ln.get("spans", []):
            text = sp.get("text", "")
            marker = text.strip()
            if _is_superscript_digit(sp, med) and marker in fn_id_map:
                parts.append(f"{{{{fn:{fn_id_map[marker]}}}}}")
            else:
                parts.append(text)
        txt = re.sub(r"[ \t]{2,}", " ", "".join(parts)).strip()
        if not txt or _is_junk_line(txt):
            continue
        line_texts.append(txt)
    return "\n".join(line_texts)


def collect_block_text_raw(block) -> str:
    """Текст блока с сохранёнными переносами строк (для стихов)."""
    pairs = _collect_lines(block)
    if not pairs:
        return ""
    out = []
    for txt, _ in pairs:
        txt = re.sub(r"[ \t]{2,}", " ", txt).strip()
        if not txt or _is_junk_line(txt):
            continue
        out.append(txt)
    return "\n".join(out)


def _is_verse_block(raw_text: str, line_count: int) -> bool:
    """Эвристика: является ли блок стихотворным.

    Критерии:
    - Минимум 3 строки
    - Средняя длина строки < 55 символов
    - Большинство строк начинаются с заглавной буквы
    - Строки не выглядят как оборванные слова (прозаический перенос)
    """
    lines = [ln for ln in raw_text.split("\n") if ln.strip()]
    if len(lines) < 3:
        return False
    avg_len = sum(len(ln) for ln in lines) / len(lines)
    if avg_len > 55:
        return False
    caps = sum(1 for ln in lines if ln and ln[0].isupper())
    if caps < len(lines) * 0.5:
        return False
    # Прозаический перенос: строка кончается строчной буквой, следующая
    # начинается со строчной — признак разрыва слова/фразы, а не стиха
    prose_wraps = 0
    for i in range(len(lines) - 1):
        cur = lines[i].rstrip(" ,;:.!?")
        nxt = lines[i + 1]
        if cur and nxt and cur[-1].islower() and nxt[0].islower():
            prose_wraps += 1
    if prose_wraps > len(lines) * 0.3:
        return False
    return True


def is_page_number(block, page_height: float, page_width: float) -> bool:
    """
    Определяет, является ли блок номером страницы.
    
    Критерии (все должны совпасть):
      1. Позиция: верхние или нижние 8% страницы
      2. Содержимое: только цифры (арабские или римские),
         возможно с окружающими тире, точками, пробелами
      3. Размер: 1 строка, не более 20 символов
    """
    x0, y0, x1, y1 = block["bbox"]
    text = block["text"].strip()
    line_count = block["line_count"]
    chars = block["chars"]
    
    # Критерий 3: короткий блок (1 строка, мало символов)
    if line_count > 1 or chars > 20:
        return False
    
    # Критерий 1: позиция — верхние или нижние 8% страницы
    margin = page_height * 0.08
    in_top = y1 < margin
    in_bottom = y0 > page_height - margin
    if not (in_top or in_bottom):
        return False
    
    # Критерий 2: содержимое — только число (арабское или римское)
    # Убираем обрамление: тире, точки, пробелы
    cleaned = re.sub(r'^[\s\-—–.*·•]+|[\s\-—–.*·•]+$', '', text)
    if not cleaned:
        return False
    
    # Арабские цифры: "42", "7", "123"
    if re.fullmatch(r'\d{1,4}', cleaned):
        return True
    
    # Римские цифры: "XII", "iv", "XLII"
    if re.fullmatch(r'[IVXLCDMivxlcdm]+', cleaned):
        # Дополнительная проверка: только валидные римские цифры
        roman_pattern = r'^[Mm]{0,3}([Cc][Mm]|[Cc][Dd]|[Dd]?[Cc]{0,3})([Xx][Cc]|[Xx][Ll]|[Ll]?[Xx]{0,3})([Ii][Xx]|[Ii][Vv]|[Vv]?[Ii]{0,3})$'
        if re.fullmatch(roman_pattern, cleaned, re.IGNORECASE):
            return True
    
    return False


def page_blocks_with_roles(page, two_columns=False, keep_page_numbers=False,
                           poetry=False, fn_counter=None):
    """Извлекает блоки со страницы, определяет роли и (опционально) сноски.

    Returns:
        (blocks, removed_page_nums, page_footnotes)
    """
    d = page.get_text("dict")
    raw_blocks = [b for b in d.get("blocks", []) if b.get("type", 0) == 0]

    page_height = page.rect.height
    page_width = page.rect.width

    # --- Phase 1: basic info for all blocks ---
    block_infos = []
    for b in raw_blocks:
        lines = b.get("lines", [])
        total_chars = 0
        wsum = 0.0
        for ln in lines:
            for sp in ln.get("spans", []):
                s = sp.get("size", 0)
                t = sp.get("text", "") or ""
                n = len(t)
                if n and s:
                    wsum += s * n
                    total_chars += n
        if total_chars == 0:
            continue
        wsize = wsum / total_chars
        block_infos.append({
            "raw": b, "wsize": wsize,
            "chars": total_chars, "line_count": len(lines),
        })

    if not block_infos:
        return [], 0, []

    all_wsizes = sorted(bi["wsize"] for bi in block_infos)
    med_size = all_wsizes[len(all_wsizes) // 2]

    # --- Phase 2: detect footnote bodies ---
    fn_body_indices: set[int] = set()
    per_page_fn: dict[str, str] = {}
    fn_id_map: dict[str, int] = {}
    page_footnotes: list[dict] = []

    if fn_counter is not None:
        for idx, bi in enumerate(block_infos):
            if _is_footnote_body_block(bi["raw"], page_height, med_size):
                for marker, text in _parse_footnote_block(bi["raw"]):
                    per_page_fn[marker] = text
                fn_body_indices.add(idx)
        for marker in sorted(per_page_fn, key=lambda m: int(m) if m.isdigit() else 0):
            gid = next(fn_counter)
            fn_id_map[marker] = gid
            page_footnotes.append({
                "id": gid, "marker": str(gid),
                "text": per_page_fn[marker],
            })

    # --- Phase 3: build blocks (skip footnote bodies) ---
    blocks = []
    for idx, bi in enumerate(block_infos):
        if idx in fn_body_indices:
            continue
        raw = bi["raw"]
        if fn_id_map:
            text = collect_block_text_fn(raw, fn_id_map)
            raw_text = collect_block_text_raw_fn(raw, fn_id_map)
        else:
            text = collect_block_text(raw)
            raw_text = collect_block_text_raw(raw)
        if not text:
            continue
        blocks.append({
            "bbox": raw.get("bbox", [0, 0, 0, 0]),
            "text": text, "raw_text": raw_text,
            "wsize": bi["wsize"],
            "line_count": bi["line_count"], "chars": bi["chars"],
        })

    # --- Page number filtering ---
    _removed_count = 0
    if not keep_page_numbers:
        filtered = []
        for b in blocks:
            if is_page_number(b, page_height, page_width):
                _removed_count += 1
            else:
                filtered.append(b)
        blocks = filtered

    # --- Heading threshold ---
    if blocks:
        sizes_sorted = sorted(b["wsize"] for b in blocks)
        med = sizes_sorted[len(sizes_sorted) // 2]
        thr = med * 1.5 + 1.0
    else:
        med = med_size
        thr = 0

    pw = page_width
    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        cx = (x0 + x1) / 2
        centered = abs(cx - pw / 2) < pw * 0.12
        wide = (x1 - x0) > pw * 0.45
        short = b["line_count"] <= 3 and b["chars"] <= 200
        big_font = b["wsize"] >= thr

        is_heading = False
        if big_font and short:
            is_heading = True
        elif centered and short and not wide and b["wsize"] >= med * 1.2:
            is_heading = True
        elif b["chars"] <= 50 and b["line_count"] == 1 and b["wsize"] >= med * 1.3:
            is_heading = True

        if is_heading:
            b["role"] = "heading"
        elif poetry and b["line_count"] >= 2:
            b["role"] = "verse"
            b["text"] = b["raw_text"]
        elif _is_verse_block(b["raw_text"], b["line_count"]):
            b["role"] = "verse"
            b["text"] = b["raw_text"]
        else:
            b["role"] = "paragraph"
        b.pop("raw_text", None)

    # --- Sort by reading order ---
    if two_columns:
        page_center_x = pw / 2
        left_blocks = [b for b in blocks
                       if (b["bbox"][0] + b["bbox"][2]) / 2 < page_center_x]
        right_blocks = [b for b in blocks
                        if (b["bbox"][0] + b["bbox"][2]) / 2 >= page_center_x]
        left_blocks.sort(key=lambda x: x["bbox"][1])
        right_blocks.sort(key=lambda x: x["bbox"][1])
        blocks = left_blocks + right_blocks
    else:
        blocks.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))

    return blocks, _removed_count, page_footnotes


def to_html(blocks, title: str) -> str:
    from html import escape as esc
    body = []
    for blk in blocks:
        text = esc(blk["text"]) if blk["text"] else ""
        if blk["role"] == "heading":
            body.append(f"<h2>{text}</h2>")
        elif blk["role"] == "verse":
            stanzas = text.split("\n\n")
            for stanza in stanzas:
                lines = stanza.split("\n")
                body.append('<div class="stanza"><p>' + "<br/>".join(lines) + "</p></div>")
        else:
            body.append(f"<p>{text}</p>")
    return (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        f"<title>{esc(title)}</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        "<style>body{font:18px/1.6 Georgia,Times,\"Times New Roman\",serif;margin:2rem;max-width:48rem;color:#111;background:#fff} h2{font-size:1.15em;margin:1.2rem 0 .6rem} p{margin:0 0 1rem} .stanza{margin:0.8em 2em} .stanza p{text-indent:0}</style>\n"
        "</head>\n<body contenteditable=\"true\" spellcheck=\"true\">\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


def main():
    ap = argparse.ArgumentParser(description="Extract structured text (paragraphs/headings) from PDF with embedded text.")
    ap.add_argument("--pdf", required=True, help="Input PDF path")
    ap.add_argument("--outdir", default="output_vol2", help="Output directory")
    ap.add_argument("--two-columns", action="store_true", help="Process pages with two columns: left column first, then right column")
    ap.add_argument("--keep-page-numbers", action="store_true", help="Не удалять номера страниц из текста (по умолчанию: удаляются)")
    ap.add_argument("--poetry", action="store_true", help="Принудительно считать все блоки (кроме заголовков) стихами — сохранять переносы строк")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    all_blocks = []
    all_footnotes: list[dict] = []
    headings_found = []
    page_nums_removed = 0
    fn_counter = itertools.count(1)

    for i in range(len(doc)):
        page = doc.load_page(i)
        p_blocks, removed, p_footnotes = page_blocks_with_roles(
            page, two_columns=args.two_columns,
            keep_page_numbers=args.keep_page_numbers,
            poetry=args.poetry,
            fn_counter=fn_counter)
        page_nums_removed += removed
        for fn in p_footnotes:
            fn["page"] = i + 1
        all_footnotes.extend(p_footnotes)
        for b in p_blocks:
            block_data = {
                "page": i + 1,
                "role": b["role"],
                "text": b["text"],
                "wsize": b["wsize"],
                "bbox": b["bbox"],
            }
            all_blocks.append(block_data)
            if b["role"] == "heading":
                headings_found.append({
                    "page": i + 1,
                    "text": b["text"][:50] + ("..." if len(b["text"]) > 50 else ""),
                    "wsize": round(b["wsize"], 1)
                })

    if getattr(args, 'debug_headings', False):
        print(f"\nНайдено заголовков: {len(headings_found)}")
        for h in headings_found:
            print(f"  Страница {h['page']}: \"{h['text']}\" (размер шрифта: {h['wsize']})")

    if page_nums_removed > 0:
        print(f"Удалено номеров страниц: {page_nums_removed}")
    elif not args.keep_page_numbers:
        print("Номера страниц не обнаружены")

    if all_footnotes:
        print(f"Обнаружено сносок: {len(all_footnotes)}")

    struct = {"file": pdf_path.name, "blocks": all_blocks}
    if all_footnotes:
        struct["footnotes"] = all_footnotes
    (outdir / "structured.json").write_text(json.dumps(struct, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "structured.html").write_text(to_html(all_blocks, pdf_path.name), encoding="utf-8")
    (outdir / "structured.txt").write_text("\n\n".join(b["text"] for b in all_blocks), encoding="utf-8")
    if all_footnotes:
        (outdir / "footnotes.json").write_text(
            json.dumps(all_footnotes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {outdir / 'structured.json'}")
    print(f"Saved: {outdir / 'structured.html'}")
    print(f"Saved: {outdir / 'structured.txt'}")


if __name__ == "__main__":
    main()


