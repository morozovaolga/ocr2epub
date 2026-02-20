import argparse
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
                           poetry=False):
    d = page.get_text("dict")
    blocks = []
    sizes = []
    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        text = collect_block_text(b)
        raw_text = collect_block_text_raw(b)
        if not text:
            continue
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
        sizes.append(wsize)
        blocks.append({
            "bbox": b.get("bbox", [0, 0, 0, 0]),
            "text": text,
            "raw_text": raw_text,
            "wsize": wsize,
            "line_count": len(lines),
            "chars": total_chars,
        })

    # Фильтрация номеров страниц
    _removed_count = 0
    if not keep_page_numbers:
        ph = page.rect.height
        pw_for_filter = page.rect.width
        filtered = []
        for b in blocks:
            if is_page_number(b, ph, pw_for_filter):
                _removed_count += 1
            else:
                filtered.append(b)
        blocks = filtered
    # Determine heading threshold per page
    # Используем более строгий порог для определения заголовков
    if blocks:
        sizes_sorted = sorted(b["wsize"] for b in blocks)
        med = sizes_sorted[len(sizes_sorted)//2]
        # Увеличиваем порог для более точного определения заголовков
        # Заголовки должны быть заметно больше обычного текста
        thr = med * 1.5 + 1.0  # Было: med * 1.35 + 0.5
    else:
        thr = 0

    pw = page.rect.width
    # Classify roles
    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        cx = (x0 + x1) / 2
        centered = abs(cx - pw / 2) < pw * 0.12
        wide = (x1 - x0) > pw * 0.45
        short = b["line_count"] <= 3 and b["chars"] <= 200
        big_font = b["wsize"] >= thr
        
        # Улучшенная логика определения заголовков
        is_heading = False
        
        # Основной критерий: большой шрифт и короткий текст
        if big_font and short:
            is_heading = True
        # Дополнительно: центрированный короткий текст (даже если шрифт не намного больше)
        elif centered and short and not wide and b["wsize"] >= med * 1.2:
            is_heading = True
        # Если текст очень короткий (1-2 слова) и шрифт больше медианы - тоже заголовок
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
    
    # Sort by reading order
    if two_columns:
        # For two-column layout: first all left column blocks (sorted by Y), then all right column blocks (sorted by Y)
        page_center_x = pw / 2
        left_blocks = []
        right_blocks = []
        for b in blocks:
            x0, y0, x1, y1 = b["bbox"]
            block_center_x = (x0 + x1) / 2
            if block_center_x < page_center_x:
                left_blocks.append(b)
            else:
                right_blocks.append(b)
        # Sort each column by Y coordinate (top to bottom)
        left_blocks.sort(key=lambda x: x["bbox"][1])
        right_blocks.sort(key=lambda x: x["bbox"][1])
        blocks = left_blocks + right_blocks
    else:
        # Default: sort by reading order (top, then left)
        blocks.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
    return blocks, _removed_count


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
    headings_found = []
    page_nums_removed = 0
    for i in range(len(doc)):
        page = doc.load_page(i)
        p_blocks, removed = page_blocks_with_roles(
            page, two_columns=args.two_columns,
            keep_page_numbers=args.keep_page_numbers,
            poetry=args.poetry)
        page_nums_removed += removed
        for b in p_blocks:
            block_data = {
                "page": i + 1,
                "role": b["role"],
                "text": b["text"],
                "wsize": b["wsize"],
                "bbox": b["bbox"],
            }
            all_blocks.append(block_data)
            
            # Сохраняем информацию о заголовках для отладки
            if b["role"] == "heading":
                headings_found.append({
                    "page": i + 1,
                    "text": b["text"][:50] + ("..." if len(b["text"]) > 50 else ""),
                    "wsize": round(b["wsize"], 1)
                })
    
    # Выводим информацию о найденных заголовках
    if getattr(args, 'debug_headings', False):
        print(f"\nНайдено заголовков: {len(headings_found)}")
        for h in headings_found:
            print(f"  Страница {h['page']}: \"{h['text']}\" (размер шрифта: {h['wsize']})")

    # Статистика по номерам страниц
    if page_nums_removed > 0:
        print(f"Удалено номеров страниц: {page_nums_removed}")
    elif not args.keep_page_numbers:
        print("Номера страниц не обнаружены")

    # Save JSON
    struct = {"file": pdf_path.name, "blocks": all_blocks}
    (outdir / "structured.json").write_text(json.dumps(struct, ensure_ascii=False, indent=2), encoding="utf-8")
    # Save initial HTML/TXT
    (outdir / "structured.html").write_text(to_html(all_blocks, pdf_path.name), encoding="utf-8")
    (outdir / "structured.txt").write_text("\n\n".join(b["text"] for b in all_blocks), encoding="utf-8")
    print(f"Saved: {outdir / 'structured.json'}")
    print(f"Saved: {outdir / 'structured.html'}")
    print(f"Saved: {outdir / 'structured.txt'}")


if __name__ == "__main__":
    main()


