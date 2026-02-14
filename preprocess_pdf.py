"""
Предобработка отсканированного PDF для улучшения качества OCR.

Операции:
  1. Выравнивание (deskew) — исправление наклона страницы
  2. Удаление шума (denoise) — удаление мелких артефактов сканирования
  3. Улучшение контраста — усиление бледного текста
  4. Адаптивная бинаризация — чёткий чёрный текст на белом фоне
  5. Удаление тёмных краёв — очистка границ от теней сканера

Зависимости: opencv-python, numpy, PyMuPDF, Pillow
LLM НЕ используется — только классическая обработка изображений.
"""

import argparse
import sys
import os

import numpy as np
import cv2
import fitz  # PyMuPDF
from PIL import Image

# Принудительно UTF-8 на Windows
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Вспомогательные функции ──────────────────────────────────────────────────


def pdf_page_to_cv2(page: fitz.Page, dpi: int = 300) -> np.ndarray:
    """Рендерит страницу PDF в numpy-массив (BGR для OpenCV)."""
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def cv2_to_pil(img: np.ndarray) -> Image.Image:
    """Конвертирует BGR numpy-массив в PIL Image (RGB)."""
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


# ── Этапы обработки ──────────────────────────────────────────────────────────


def deskew(img: np.ndarray, max_angle: float = 5.0) -> np.ndarray:
    """
    Определяет угол наклона текста и выравнивает страницу.
    Использует проекционный профиль (сумма пикселей по строкам):
    при правильном угле строки текста дают максимальную дисперсию.
    max_angle — максимальный угол коррекции (градусы).
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    # Бинаризация для анализа
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Поиск угла через minAreaRect на ненулевых точках
    coords = np.column_stack(np.where(bw > 0))
    if len(coords) < 100:
        return img  # Слишком мало текста — не трогаем

    angle = cv2.minAreaRect(coords)[-1]

    # minAreaRect возвращает угол от -90 до 0; нормализуем
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Ограничиваем угол
    if abs(angle) > max_angle or abs(angle) < 0.1:
        return img

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def denoise(img: np.ndarray, strength: int = 10) -> np.ndarray:
    """
    Удаляет шум из изображения.
    strength: сила фильтра (чем больше, тем агрессивнее).
    Для цветных изображений используется fastNlMeansDenoisingColored.
    """
    if len(img.shape) == 3:
        return cv2.fastNlMeansDenoisingColored(img, None, strength, strength, 7, 21)
    else:
        return cv2.fastNlMeansDenoising(img, None, strength, 7, 21)


def enhance_contrast(img: np.ndarray, clip_limit: float = 2.0,
                     tile_size: int = 8) -> np.ndarray:
    """
    Улучшает контраст через CLAHE (Contrast Limited Adaptive Histogram Equalization).
    Работает в цветовом пространстве LAB, чтобы не искажать цвета.
    clip_limit: ограничение контраста (2.0 — умеренно).
    tile_size: размер плитки для локальной адаптации.
    """
    if len(img.shape) == 2:
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
        return clahe.apply(img)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    l_ch = clahe.apply(l_ch)
    lab = cv2.merge([l_ch, a_ch, b_ch])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def adaptive_binarize(img: np.ndarray, block_size: int = 51,
                      C: int = 10) -> np.ndarray:
    """
    Адаптивная бинаризация: чёрный текст на белом фоне.
    block_size: размер окрестности для адаптивного порога (нечётное число).
    C: константа вычитания из среднего.
    Результат — 3-канальное BGR-изображение для совместимости с пайплайном.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    bw = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size, C
    )
    # Возвращаем 3-канальное изображение
    return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)


def clean_borders(img: np.ndarray, margin_pct: float = 1.0) -> np.ndarray:
    """
    Удаляет тёмные края (тени от сканера), заменяя их белым.
    margin_pct: процент от размера страницы для анализа краёв.
    """
    h, w = img.shape[:2]
    margin_x = max(1, int(w * margin_pct / 100))
    margin_y = max(1, int(h * margin_pct / 100))

    result = img.copy()
    white = [255, 255, 255] if len(img.shape) == 3 else 255

    # Анализируем каждый край
    for region, (y1, y2, x1, x2) in [
        ("top", (0, margin_y, 0, w)),
        ("bottom", (h - margin_y, h, 0, w)),
        ("left", (0, h, 0, margin_x)),
        ("right", (0, h, w - margin_x, w)),
    ]:
        strip = img[y1:y2, x1:x2]
        gray_strip = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if len(strip.shape) == 3 else strip
        mean_val = np.mean(gray_strip)
        # Если край тёмный (среднее < 200), заливаем белым
        if mean_val < 200:
            result[y1:y2, x1:x2] = white

    return result


def remove_small_noise(img: np.ndarray, min_area: int = 20) -> np.ndarray:
    """
    Удаляет мелкие чёрные пятна (мусор от сканера) через морфологию.
    min_area: минимальная площадь компоненты для сохранения (в пикселях).
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Ищем связные компоненты
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)

    # Маска для удаления мелких компонент
    mask = np.zeros_like(bw)
    for i in range(1, num_labels):  # Пропускаем фон (0)
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            mask[labels == i] = 255

    # Восстанавливаем изображение: оставляем только крупные компоненты
    clean = np.full_like(gray, 255)
    clean[mask > 0] = gray[mask > 0]

    if len(img.shape) == 3:
        return cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR)
    return clean


def sharpen_text(img: np.ndarray, amount: float = 0.5) -> np.ndarray:
    """
    Лёгкая резкость для размытых сканов через unsharp mask.
    amount: сила резкости (0.0—1.0).
    """
    blurred = cv2.GaussianBlur(img, (0, 0), 3)
    sharpened = cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)
    return sharpened


# ── Пресеты обработки ────────────────────────────────────────────────────────


PRESETS = {
    "light": {
        "description": "Лёгкая: контраст + лёгкий шумодав. Для относительно чистых сканов.",
        "steps": ["contrast", "denoise_light"],
    },
    "medium": {
        "description": "Средняя: контраст + шумодав + выравнивание + резкость. Рекомендуется по умолчанию.",
        "steps": ["deskew", "denoise", "contrast", "sharpen", "clean_borders"],
    },
    "heavy": {
        "description": "Тяжёлая: полная обработка + удаление мусора. Для очень грязных сканов.",
        "steps": ["deskew", "clean_borders", "denoise_heavy", "contrast_strong",
                  "remove_noise", "sharpen"],
    },
    "binarize": {
        "description": "Бинаризация: чёрно-белое. Для максимального качества OCR на бледных сканах.",
        "steps": ["deskew", "denoise", "contrast", "binarize", "remove_noise",
                  "clean_borders"],
    },
}


def apply_step(img: np.ndarray, step: str) -> np.ndarray:
    """Применяет один шаг обработки."""
    if step == "deskew":
        return deskew(img)
    elif step == "denoise_light":
        return denoise(img, strength=5)
    elif step == "denoise":
        return denoise(img, strength=10)
    elif step == "denoise_heavy":
        return denoise(img, strength=15)
    elif step == "contrast":
        return enhance_contrast(img, clip_limit=2.0)
    elif step == "contrast_strong":
        return enhance_contrast(img, clip_limit=3.5)
    elif step == "sharpen":
        return sharpen_text(img, amount=0.5)
    elif step == "binarize":
        return adaptive_binarize(img, block_size=51, C=10)
    elif step == "clean_borders":
        return clean_borders(img, margin_pct=1.0)
    elif step == "remove_noise":
        return remove_small_noise(img, min_area=20)
    else:
        print(f"  Неизвестный шаг: {step}, пропускаю")
        return img


# ── Основная функция ─────────────────────────────────────────────────────────


def preprocess_pdf(input_pdf: str, output_pdf: str, preset: str = "medium",
                   dpi: int = 300, pages: str = "",
                   custom_steps: str = "") -> bool:
    """
    Обрабатывает PDF: рендерит страницы, применяет пресет/шаги, сохраняет новый PDF.

    Args:
        input_pdf: путь к входному PDF
        output_pdf: путь к выходному PDF
        preset: имя пресета ('light', 'medium', 'heavy', 'binarize')
        dpi: разрешение рендеринга (по умолчанию 300)
        pages: диапазон страниц ("1-10", "1,3,5", "" = все)
        custom_steps: шаги через запятую (переопределяет пресет)

    Returns:
        True при успехе
    """
    # Определяем шаги обработки
    if custom_steps:
        steps = [s.strip() for s in custom_steps.split(",") if s.strip()]
        print(f"Пользовательские шаги: {', '.join(steps)}")
    elif preset in PRESETS:
        steps = PRESETS[preset]["steps"]
        print(f"Пресет: {preset} — {PRESETS[preset]['description']}")
    else:
        print(f"Неизвестный пресет '{preset}', использую 'medium'")
        preset = "medium"
        steps = PRESETS["medium"]["steps"]

    print(f"Шаги обработки: {' -> '.join(steps)}")
    print(f"DPI: {dpi}")

    # Открываем PDF
    doc = fitz.open(input_pdf)
    total_pages = len(doc)
    print(f"Всего страниц в PDF: {total_pages}")

    # Определяем какие страницы обрабатывать
    page_indices = _parse_pages(pages, total_pages)
    print(f"Обрабатываем страниц: {len(page_indices)}")

    # Создаём выходной PDF
    out_doc = fitz.open()

    for idx, page_num in enumerate(page_indices):
        page = doc.load_page(page_num)
        pct = (idx + 1) * 100 // len(page_indices)
        print(f"\r  Страница {page_num + 1}/{total_pages} "
              f"[{'#' * (pct // 5)}{'.' * (20 - pct // 5)}] {pct}%",
              end="", flush=True)

        # Рендерим в изображение
        img = pdf_page_to_cv2(page, dpi=dpi)

        # Применяем шаги
        for step in steps:
            img = apply_step(img, step)

        # Конвертируем обратно в PDF-страницу
        pil_img = cv2_to_pil(img)

        # Сохраняем как страницу PDF
        img_bytes = _pil_to_pdf_bytes(pil_img, dpi)
        img_pdf = fitz.open("pdf", img_bytes)
        out_doc.insert_pdf(img_pdf)
        img_pdf.close()

    print()  # Новая строка после прогресс-бара

    # Сохраняем
    out_doc.save(output_pdf, garbage=4, deflate=True, deflate_images=True)
    out_doc.close()
    doc.close()

    input_size = os.path.getsize(input_pdf) / (1024 * 1024)
    output_size = os.path.getsize(output_pdf) / (1024 * 1024)
    print(f"\nГотово!")
    print(f"  Вход:  {input_pdf} ({input_size:.1f} МБ)")
    print(f"  Выход: {output_pdf} ({output_size:.1f} МБ)")
    return True


def _pil_to_pdf_bytes(pil_img: Image.Image, dpi: int) -> bytes:
    """Конвертирует PIL Image в PDF-байты (одна страница)."""
    import io
    buf = io.BytesIO()
    # Сохраняем как PDF через Pillow
    pil_img.save(buf, format="PDF", resolution=dpi)
    return buf.getvalue()


def _parse_pages(pages_str: str, total: int) -> list:
    """
    Парсит строку с диапазоном страниц.
    '' -> все страницы
    '1-10' -> страницы 1-10
    '1,3,5-7' -> страницы 1, 3, 5, 6, 7
    """
    if not pages_str.strip():
        return list(range(total))

    indices = set()
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            start = max(0, int(a.strip()) - 1)
            end = min(total, int(b.strip()))
            indices.update(range(start, end))
        else:
            idx = int(part.strip()) - 1
            if 0 <= idx < total:
                indices.add(idx)

    return sorted(indices)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Предобработка PDF-сканов для улучшения качества OCR (без LLM)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Пресеты:
  light    — лёгкая: контраст + лёгкий шумодав
  medium   — средняя: выравнивание + шумодав + контраст + резкость (рекомендуется)
  heavy    — тяжёлая: полная обработка + удаление мусора
  binarize — бинаризация: чёрно-белое для максимального OCR

Примеры:
  python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf
  python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --preset heavy
  python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --preset binarize --dpi 400
  python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --pages 1-50
  python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --steps deskew,contrast,sharpen

Доступные шаги для --steps:
  deskew          — выравнивание наклона
  denoise_light   — лёгкий шумодав
  denoise         — средний шумодав
  denoise_heavy   — сильный шумодав
  contrast        — улучшение контраста (CLAHE)
  contrast_strong — сильное улучшение контраста
  sharpen         — резкость текста
  binarize        — адаптивная бинаризация (чёрно-белое)
  clean_borders   — очистка тёмных краёв
  remove_noise    — удаление мелкого мусора
        """
    )
    parser.add_argument("--pdf", required=True, help="Входной PDF файл")
    parser.add_argument("--out", required=True, help="Выходной PDF файл")
    parser.add_argument("--preset", default="medium",
                        choices=list(PRESETS.keys()),
                        help="Пресет обработки (по умолчанию: medium)")
    parser.add_argument("--dpi", type=int, default=300,
                        help="DPI рендеринга страниц (по умолчанию: 300)")
    parser.add_argument("--pages", default="",
                        help="Диапазон страниц: '1-10' или '1,3,5-7' (по умолчанию: все)")
    parser.add_argument("--steps", default="",
                        help="Шаги через запятую (переопределяет пресет)")

    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"Ошибка: файл не найден: {args.pdf}")
        return 1

    print("=" * 80)
    print("ПРЕДОБРАБОТКА PDF ДЛЯ OCR")
    print("=" * 80)

    success = preprocess_pdf(
        input_pdf=args.pdf,
        output_pdf=args.out,
        preset=args.preset,
        dpi=args.dpi,
        pages=args.pages,
        custom_steps=args.steps,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
