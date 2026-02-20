"""
OCR-движок с поддержкой нескольких бэкендов и автоматическим фоллбэком.

Поддерживаемые движки:
  - pymupdf  — извлечение встроенного текстового слоя (по умолчанию, быстро)
  - easyocr  — нейросетевой OCR (pytorch, хорошее качество на русском)
  - tesseract — классический OCR (требует системной установки)
  - doctr    — end-to-end OCR (pytorch, по умолчанию обучен на латинице)
  - auto     — pymupdf, с фоллбэком на easyocr если текста мало

Использование из командной строки:
  python ocr_engine.py --pdf book.pdf --outdir out --engine auto --dpi 300
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

import fitz
import numpy as np
from PIL import Image

MIN_CHARS_PER_PAGE = 50  # порог для auto-фоллбэка

AVAILABLE_ENGINES = {}


def _register_engines():
    """Проверяет доступность OCR-движков при импорте."""
    AVAILABLE_ENGINES["pymupdf"] = True

    try:
        import easyocr  # noqa: F401
        AVAILABLE_ENGINES["easyocr"] = True
    except ImportError:
        AVAILABLE_ENGINES["easyocr"] = False

    try:
        import pytesseract
        _tess_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for tp in _tess_paths:
            if os.path.isfile(tp):
                pytesseract.pytesseract.tesseract_cmd = tp
                break
        _user_tessdata = os.path.expanduser("~/.tessdata")
        if os.path.isdir(_user_tessdata):
            os.environ.setdefault("TESSDATA_PREFIX", _user_tessdata)
        pytesseract.get_tesseract_version()
        AVAILABLE_ENGINES["tesseract"] = True
    except Exception:
        AVAILABLE_ENGINES["tesseract"] = False

    try:
        from doctr.models import ocr_predictor  # noqa: F401
        AVAILABLE_ENGINES["doctr"] = True
    except ImportError:
        AVAILABLE_ENGINES["doctr"] = False


_register_engines()


# ---------------------------------------------------------------------------
# Рендеринг страниц PDF в изображения
# ---------------------------------------------------------------------------

def render_page(doc, page_idx: int, dpi: int = 300) -> np.ndarray:
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    return img


# ---------------------------------------------------------------------------
# PyMuPDF: извлечение встроенного текстового слоя
# ---------------------------------------------------------------------------

def _ocr_pymupdf_page(doc, page_idx: int, poetry: bool = False) -> list[dict]:
    """Извлекает блоки текста из встроенного слоя PDF."""
    page = doc[page_idx]
    d = page.get_text("dict")
    blocks = []
    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        lines = b.get("lines", [])
        line_pairs = []
        total_chars = 0
        wsum = 0.0
        for ln in lines:
            spans = ln.get("spans", [])
            txt = "".join(sp.get("text", "") for sp in spans)
            x0 = ln.get("bbox", [0])[0]
            line_pairs.append((txt, x0))
            for sp in spans:
                s = sp.get("size", 0)
                t = sp.get("text", "") or ""
                n = len(t)
                if n and s:
                    wsum += s * n
                    total_chars += n
        text = "\n".join(t for t, _ in line_pairs)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if poetry:
            out = []
            for txt, _ in line_pairs:
                txt = re.sub(r"[ \t]{2,}", " ", txt).strip()
                if not txt:
                    continue
                if _is_junk_block(txt):
                    continue
                out.append(txt)
            text = "\n".join(out)
        else:
            text = re.sub(r"(\w)[\-\u2010\u2013\u2014]\n(?=\w)", r"\1", text)
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
            text = re.sub(r"[ \t]{2,}", " ", text)
            text = text.strip()
        if not text or total_chars == 0:
            continue
        wsize = wsum / total_chars if total_chars else 0
        blocks.append({
            "bbox": list(b.get("bbox", [0, 0, 0, 0])),
            "text": text,
            "wsize": round(wsize, 2),
            "line_count": len(lines),
            "chars": total_chars,
        })
    return blocks


# ---------------------------------------------------------------------------
# EasyOCR
# ---------------------------------------------------------------------------

_easyocr_reader = None


def _get_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(["ru", "en"], gpu=False)
    return _easyocr_reader


def _ocr_easyocr_page(img: np.ndarray, scale: float = 1.0) -> list[dict]:
    reader = _get_easyocr()
    results = reader.readtext(img)
    blocks = []
    for bbox_pts, text, conf in results:
        text = text.strip()
        if not text:
            continue
        xs = [p[0] for p in bbox_pts]
        ys = [p[1] for p in bbox_pts]
        bbox = [min(xs) / scale, min(ys) / scale,
                max(xs) / scale, max(ys) / scale]
        blocks.append({
            "bbox": [round(v, 1) for v in bbox],
            "text": text,
            "wsize": 12.0,
            "line_count": 1,
            "chars": len(text),
        })
    return blocks


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------

def _ocr_tesseract_page(img: np.ndarray, scale: float = 1.0) -> list[dict]:
    import pytesseract
    pil_img = Image.fromarray(img)
    data = pytesseract.image_to_data(pil_img, lang="rus", output_type=pytesseract.Output.DICT)

    blocks = {}
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        block_num = data["block_num"][i]
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        if block_num not in blocks:
            blocks[block_num] = {
                "texts": [],
                "x0": x, "y0": y, "x1": x + w, "y1": y + h,
            }
        b = blocks[block_num]
        b["texts"].append(text)
        b["x0"] = min(b["x0"], x)
        b["y0"] = min(b["y0"], y)
        b["x1"] = max(b["x1"], x + w)
        b["y1"] = max(b["y1"], y + h)

    result = []
    for b in blocks.values():
        text = " ".join(b["texts"])
        if not text.strip():
            continue
        bbox = [b["x0"] / scale, b["y0"] / scale,
                b["x1"] / scale, b["y1"] / scale]
        result.append({
            "bbox": [round(v, 1) for v in bbox],
            "text": text,
            "wsize": 12.0,
            "line_count": 1,
            "chars": len(text),
        })
    return result


# ---------------------------------------------------------------------------
# DocTR
# ---------------------------------------------------------------------------

_doctr_model = None


def _get_doctr():
    global _doctr_model
    if _doctr_model is None:
        from doctr.models import ocr_predictor
        _doctr_model = ocr_predictor(
            det_arch="db_resnet50", reco_arch="crnn_vgg16_bn", pretrained=True
        )
    return _doctr_model


def _ocr_doctr_page(img: np.ndarray, scale: float = 1.0) -> list[dict]:
    from doctr.io import DocumentFile

    model = _get_doctr()
    pil_img = Image.fromarray(img)
    tmp_path = "_doctr_tmp.png"
    pil_img.save(tmp_path)
    doc = DocumentFile.from_images(tmp_path)
    result = model(doc)
    os.remove(tmp_path)

    h_img, w_img = img.shape[:2]
    blocks = []
    for page in result.pages:
        for block in page.blocks:
            block_texts = []
            x0, y0, x1, y1 = 1.0, 1.0, 0.0, 0.0
            for line in block.lines:
                line_text = " ".join(w.value for w in line.words)
                block_texts.append(line_text)
                geo = line.geometry
                x0 = min(x0, geo[0][0])
                y0 = min(y0, geo[0][1])
                x1 = max(x1, geo[1][0])
                y1 = max(y1, geo[1][1])
            text = "\n".join(block_texts)
            if not text.strip():
                continue
            bbox = [x0 * w_img / scale, y0 * h_img / scale,
                    x1 * w_img / scale, y1 * h_img / scale]
            blocks.append({
                "bbox": [round(v, 1) for v in bbox],
                "text": text,
                "wsize": 12.0,
                "line_count": len(block_texts),
                "chars": len(text),
            })
    return blocks


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

OCR_FUNCS = {
    "easyocr": _ocr_easyocr_page,
    "tesseract": _ocr_tesseract_page,
    "doctr": _ocr_doctr_page,
}


_JUNK_RE = re.compile(r"^[\s\-–—&*•·#§†‡©®™°¬]+$")


def _is_junk_block(text: str) -> bool:
    """Безусловный мусор: только спецсимволы, без букв и цифр."""
    if not text:
        return True
    if _JUNK_RE.fullmatch(text):
        return True
    return False


def _is_page_number(block: dict, page_height: float) -> bool:
    """Номер страницы: короткий числовой блок вверху/внизу страницы."""
    text = block.get("text", "").strip()
    if not text or len(text) > 10:
        return False
    bbox = block.get("bbox", [0, 0, 0, 0])
    y0, y1 = bbox[1], bbox[3]
    margin = page_height * 0.10
    if not (y1 < margin or y0 > page_height - margin):
        return False
    cleaned = re.sub(r'^[\s\-—–.*·•]+|[\s\-—–.*·•]+$', '', text)
    if not cleaned:
        return False
    if re.fullmatch(r'\d{1,4}', cleaned):
        return True
    if re.fullmatch(r'[IVXLCDMivxlcdm]+', cleaned):
        return True
    return False


def ocr_pdf(
    pdf_path: str,
    engine: str = "auto",
    pages: list[int] | None = None,
    dpi: int = 300,
    two_columns: bool = False,
    poetry: bool = False,
) -> dict:
    """Распознаёт текст из PDF.

    Args:
        pdf_path: путь к PDF-файлу
        engine: "auto", "pymupdf", "easyocr", "tesseract", "doctr"
        pages: список номеров страниц (1-based) или None для всех
        dpi: разрешение рендеринга для OCR-движков
        two_columns: двухколоночный layout

    Returns:
        dict в формате {"file": ..., "blocks": [...]} совместимый с
        extract_structured_text.py
    """
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if pages:
        page_indices = [p - 1 for p in pages if 1 <= p <= total_pages]
    else:
        page_indices = list(range(total_pages))

    scale = dpi / 72
    all_blocks = []
    ocr_used_pages = 0

    for page_idx in page_indices:
        page_num = page_idx + 1

        if engine == "auto":
            blocks = _ocr_pymupdf_page(doc, page_idx, poetry=poetry)
            text_len = sum(b["chars"] for b in blocks)
            if text_len < MIN_CHARS_PER_PAGE:
                fallback = _pick_best_engine()
                if fallback:
                    img = render_page(doc, page_idx, dpi)
                    blocks = OCR_FUNCS[fallback](img, scale)
                    ocr_used_pages += 1
        elif engine == "pymupdf":
            blocks = _ocr_pymupdf_page(doc, page_idx, poetry=poetry)
        else:
            if engine not in OCR_FUNCS:
                raise ValueError(f"Неизвестный OCR-движок: {engine}. "
                                 f"Доступны: {list(OCR_FUNCS.keys())}")
            if not AVAILABLE_ENGINES.get(engine):
                raise RuntimeError(f"OCR-движок '{engine}' не установлен. "
                                   f"Установите: pip install {engine}")
            img = render_page(doc, page_idx, dpi)
            blocks = OCR_FUNCS[engine](img, scale)
            ocr_used_pages += 1

        if two_columns:
            pw = doc[page_idx].rect.width
            center = pw / 2
            left = [b for b in blocks if (b["bbox"][0] + b["bbox"][2]) / 2 < center]
            right = [b for b in blocks if (b["bbox"][0] + b["bbox"][2]) / 2 >= center]
            left.sort(key=lambda b: b["bbox"][1])
            right.sort(key=lambda b: b["bbox"][1])
            blocks = left + right
        else:
            blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        page_height = doc[page_idx].rect.height
        filtered = []
        for b in blocks:
            text = b.get("text", "").strip()
            if _is_junk_block(text):
                continue
            if _is_page_number(b, page_height):
                continue
            b["page"] = page_num
            if poetry and "\n" in text:
                b["role"] = "verse"
            else:
                b["role"] = "paragraph"
            filtered.append(b)
        all_blocks.extend(filtered)

    doc.close()

    if ocr_used_pages:
        print(f"OCR применён к {ocr_used_pages} из {len(page_indices)} страниц")

    return {
        "file": Path(pdf_path).name,
        "blocks": all_blocks,
    }


def _pick_best_engine() -> str | None:
    for eng in ("easyocr", "tesseract", "doctr"):
        if AVAILABLE_ENGINES.get(eng):
            return eng
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="OCR-движок: извлечение текста из PDF с поддержкой нескольких бэкендов"
    )
    ap.add_argument("--pdf", required=True, help="Путь к PDF-файлу")
    ap.add_argument("--outdir", default="out", help="Папка для результатов")
    ap.add_argument("--engine", default="auto",
                    choices=["auto", "pymupdf", "easyocr", "tesseract", "doctr"],
                    help="OCR-движок (по умолчанию: auto)")
    ap.add_argument("--dpi", type=int, default=300,
                    help="DPI рендеринга для OCR (по умолчанию: 300)")
    ap.add_argument("--pages", help="Страницы: '1-10' или '1,3,5-7'")
    ap.add_argument("--two-columns", action="store_true",
                    help="Двухколоночный layout")
    ap.add_argument("--poetry", action="store_true",
                    help="Режим стихов: сохранять переносы строк")
    args = ap.parse_args()

    page_list = _parse_pages(args.pages) if args.pages else None

    print(f"PDF: {args.pdf}")
    print(f"Движок: {args.engine}")
    print(f"DPI: {args.dpi}")
    avail = [k for k, v in AVAILABLE_ENGINES.items() if v]
    print(f"Доступные движки: {', '.join(avail)}")

    t0 = time.time()
    result = ocr_pdf(
        args.pdf, engine=args.engine, pages=page_list,
        dpi=args.dpi, two_columns=args.two_columns,
        poetry=args.poetry,
    )
    elapsed = time.time() - t0
    print(f"Обработано блоков: {len(result['blocks'])} за {elapsed:.1f}s")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    json_path = outdir / "structured.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    txt_path = outdir / "structured.txt"
    txt_path.write_text(
        "\n\n".join(b["text"] for b in result["blocks"]),
        encoding="utf-8",
    )
    print(f"Сохранено: {json_path}")
    print(f"Сохранено: {txt_path}")


def _parse_pages(s: str) -> list[int]:
    pages = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


if __name__ == "__main__":
    main()
