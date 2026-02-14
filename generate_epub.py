import argparse
import json
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from html import escape as hesc
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _round_to_multiple(value: int, step: int = 64, minimum: int = 64, maximum: int = 1024) -> int:
    if value <= 0:
        return minimum
    adjusted = ((value + step - 1) // step) * step
    return max(minimum, min(maximum, adjusted))


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or not all(ch in "0123456789abcdefABCDEF" for ch in raw):
        raise ValueError(f"Неверный HEX-цвет: {value}")
    return tuple(int(raw[i:i+2], 16) for i in (0, 2, 4))


def _coerce_cover_colors(colors, expected: int = 5):
    if not colors:
        return None
    normalized = []
    for color in colors:
        if color is None:
            continue
        if isinstance(color, tuple):
            if len(color) != 3:
                continue
            normalized.append(tuple(max(0, min(255, int(c))) for c in color))
        else:
            try:
                normalized.append(_hex_to_rgb(str(color)))
            except ValueError:
                return None
        if len(normalized) >= expected:
            break
    if len(normalized) < expected:
        return None
    return normalized[:expected]


def parse_cover_colors_arg(value: str, expected_count: int = 5) -> list[str]:
    raw = value.strip()
    if not raw:
        raise ValueError("Пустая строка цвета")
    parts = [part.strip() for part in re.split(r"[\s,;]+", raw) if part.strip()]
    if len(parts) < expected_count:
        raise ValueError(f"Нужно указать {expected_count} HEX-цветов (stripe, top, title, art-start, art-end)")
    return parts[:expected_count]


def is_page_number(text: str) -> bool:
    """Проверить, является ли текст номером страницы.
    
    Номера страниц обычно:
    - Только цифры (1-999 обычно)
    - Короткие (до 3-4 цифр)
    - Могут быть с пробелами, но очень короткие
    - НЕ являются заголовками глав (которые могут быть "I", "II", "1", "2" и т.д.)
    
    Возвращает True, если это номер страницы (нужно удалить).
    """
    if not text:
        return False
    
    stripped = text.strip()
    
    # Только цифры - вероятно номер страницы
    if stripped.isdigit():
        # Номера страниц обычно небольшие (до 999)
        # Но заголовки глав тоже могут быть цифрами, поэтому проверяем контекст
        # Если это очень короткая строка (1-3 символа) и только цифры - вероятно номер страницы
        if len(stripped) <= 3:
            return True
        # Если это 4+ цифры, но меньше 1000 - тоже может быть номер страницы
        if len(stripped) <= 4 and int(stripped) < 1000:
            return True
    
    # Цифры с пробелами, но очень короткие (до 5 символов всего)
    if len(stripped) <= 5 and stripped.replace(' ', '').isdigit():
        return True
    
    return False


def load_blocks_from_json(json_path: Path):
    """Загрузить блоки из JSON файла (structured.json или structured_rules.json)
    
    Автоматически фильтрует номера страниц (маленькие числа внизу страницы).
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    
    # Фильтруем номера страниц
    filtered_blocks = []
    for block in blocks:
        text = block.get("text", "").strip()
        if not text:
            continue
        # Пропускаем номера страниц (маленькие числа внизу страницы - не метки глав!)
        if is_page_number(text):
            continue
        filtered_blocks.append(block)
    
    return filtered_blocks


def looks_like_section_heading(line: str) -> bool:
    """Проверить, начинается ли строка с признака заголовка.
    
    Распознает:
    - Ключевые слова: Часть, Глава, Раздел, Книга
    - Римские цифры: I, II, III, IV, V, VI, VII, VIII, IX, X и т.д.
    - Арабские цифры: 1, 2, 3 и т.д.
    - Большие буквы: A, B, C и т.д.
    - Разделители: ***
    """
    if not line:
        return False
    stripped = line.strip()
    if not stripped:
        return False

    # Ключевые слова с цифрами: "Часть 1", "Глава II" и т.д.
    keyword_pattern = re.compile(r'^(?:Часть|Глава|Раздел|Книга)\s*[IVXLCDM\d]+', re.IGNORECASE)
    if keyword_pattern.match(stripped):
        return True

    # Римские цифры в начале строки: "I.", "II", "III. Название" и т.д.
    # Поддерживаем основные римские цифры: I, V, X, L, C, D, M
    roman_pattern = re.compile(r'^[IVXLCDM]+[\.\)\s]', re.IGNORECASE)
    if roman_pattern.match(stripped):
        return True
    
    # Только римские цифры (если строка короткая, вероятно заголовок)
    if len(stripped) <= 10 and re.match(r'^[IVXLCDM]+$', stripped, re.IGNORECASE):
        return True

    # Арабские цифры в начале строки: "1.", "2", "3. Название" и т.д.
    arabic_pattern = re.compile(r'^\d+[\.\)\s]', re.IGNORECASE)
    if arabic_pattern.match(stripped):
        return True
    
    # Только арабские цифры (если строка короткая, вероятно заголовок)
    if len(stripped) <= 10 and re.match(r'^\d+$', stripped):
        return True

    # Большие буквы в начале строки: "A.", "B", "C. Название" и т.д.
    # Проверяем, что это одна буква или буква с точкой/скобкой
    letter_pattern = re.compile(r'^[А-ЯЁA-Z][\.\)\s]', re.IGNORECASE)
    if letter_pattern.match(stripped):
        # Убеждаемся, что это не начало обычного предложения
        # Если после буквы идет точка и пробел, а затем заглавная буква - вероятно заголовок
        if re.match(r'^[А-ЯЁA-Z]\.\s+[А-ЯЁA-Z]', stripped, re.IGNORECASE):
            return True
        # Если это просто одна буква с точкой или скобкой - заголовок
        if re.match(r'^[А-ЯЁA-Z][\.\)]\s*$', stripped, re.IGNORECASE):
            return True
    
    # Только одна большая буква (если строка очень короткая)
    if len(stripped) <= 3 and re.match(r'^[А-ЯЁA-Z]$', stripped):
        return True

    # Разделители: ***, --- и т.д.
    if re.match(r'^\*\s*\*\s*\*', stripped):
        return True
    
    if re.match(r'^[-=]{3,}', stripped):
        return True

    return False


def paragraphs_to_blocks(paragraphs):
    """Преобразовать список абзацев в блоки (heading/paragraph)"""
    blocks = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        lines = [line.strip() for line in para.splitlines() if line.strip()]
        if not lines:
            continue

        first_line = lines[0]
        
        # Улучшенная логика определения заголовков
        is_heading = False
        
        # Проверка на стандартные признаки заголовка раздела
        if looks_like_section_heading(first_line):
            is_heading = True
        # Короткая строка (до 100 символов), все заглавные или очень короткая (до 50 символов)
        elif len(first_line) < 100:
            if first_line.isupper() and not first_line.endswith(('.', ',', ';', ':', '!', '?')):
                is_heading = True
            elif len(lines) == 1 and len(first_line) < 50 and not first_line.endswith(('.', ',', ';', ':', '!', '?')):
                # Одна строка, короткая, без знаков препинания в конце
                is_heading = True
            elif len(first_line) <= 30 and not first_line.endswith(('.', ',', ';', ':', '!', '?')):
                # Очень короткая строка без знаков препинания - вероятно заголовок
                is_heading = True
            # Дополнительная проверка: паттерны с римскими цифрами, арабскими цифрами, буквами
            elif re.match(r'^[IVXLCDM]+\s+[А-ЯЁA-Z]', first_line, re.IGNORECASE):
                # Римская цифра + текст
                is_heading = True
            elif re.match(r'^\d+\s+[А-ЯЁA-Z]', first_line):
                # Арабская цифра + текст
                is_heading = True
            elif re.match(r'^[А-ЯЁA-Z]\.\s+[А-ЯЁA-Z]', first_line, re.IGNORECASE):
                # Буква с точкой + текст
                is_heading = True

        para_text = ' '.join(lines).strip()
        para_text = re.sub(r'\s+', ' ', para_text)

        if para_text:
            blocks.append({
                "role": "heading" if is_heading else "paragraph",
                "text": para_text
            })
    return blocks


def load_blocks_from_html(html_path: Path):
    """Загрузить блоки из HTML файла (парсит h2, p и pre теги)
    
    Автоматически фильтрует номера страниц (маленькие числа внизу страницы).
    """
    html = html_path.read_text(encoding="utf-8")
    blocks = []
    
    # Сначала пробуем найти h2 и p теги (формат modernize_structured.py)
    h2_pattern = r'<h2[^>]*>(.*?)</h2>'
    p_pattern = r'<p[^>]*>(.*?)</p>'
    pre_pattern = r'<pre[^>]*>(.*?)</pre>'
    
    # Проверяем, есть ли pre тег (формат lt_cloud.py)
    pre_match = re.search(pre_pattern, html, re.DOTALL | re.IGNORECASE)
    
    if pre_match:
        pre_content = pre_match.group(1)
        # Декодируем HTML entities
        pre_content = pre_content.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
        # Убираем HTML теги, если есть
        pre_content = re.sub(r'<[^>]+>', '', pre_content)
        
        paragraphs = re.split(r'\n\s*\n', pre_content)
        blocks.extend(paragraphs_to_blocks(paragraphs))
    else:
        pos = 0
        while pos < len(html):
            h2_match = re.search(h2_pattern, html[pos:], re.DOTALL | re.IGNORECASE)
            p_match = re.search(p_pattern, html[pos:], re.DOTALL | re.IGNORECASE)
            
            if h2_match and (not p_match or h2_match.start() < p_match.start()):
                text = h2_match.group(1)
                text = re.sub(r'<mark[^>]*>(.*?)</mark>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', '', text)
                text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
                text = re.sub(r'\s+', ' ', text).strip()
                # Пропускаем номера страниц (маленькие числа внизу страницы - не метки глав!)
                if text and not is_page_number(text):
                    blocks.append({"role": "heading", "text": text})
                pos += h2_match.end()
            elif p_match:
                text = p_match.group(1)
                text = re.sub(r'<mark[^>]*>(.*?)</mark>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', '', text)
                text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
                text = re.sub(r'\s+', ' ', text).strip()
                # Пропускаем номера страниц (маленькие числа внизу страницы - не метки глав!)
                if text and not is_page_number(text):
                    blocks.append({"role": "paragraph", "text": text})
                pos += p_match.end()
            else:
                break
    
    return blocks


def load_blocks_from_text(text: str):
    """Загрузить блоки из plain text файла (final_clean.txt или final.txt)
    
    Автоматически фильтрует номера страниц (маленькие числа внизу страницы).
    """
    # Убираем пробелы в начале и конце
    text = text.strip()
    if not text:
        return []
    
    # Сначала пробуем разбить по двойным переносам строк (абзацы)
    paragraphs = re.split(r'\n\s*\n+', text)
    
    # Если абзацев мало или нет, пробуем разбить по одинарным переносам
    # но только если текст действительно большой
    if len(paragraphs) <= 1 and '\n' in text:
        # Разбиваем по одинарным переносам, но объединяем короткие строки
        lines = text.splitlines()
        paragraphs = []
        current_para = []
        for line in lines:
            line = line.strip()
            if not line:
                if current_para:
                    paragraphs.append('\n'.join(current_para))
                    current_para = []
            else:
                current_para.append(line)
        # Добавляем последний абзац
        if current_para:
            paragraphs.append('\n'.join(current_para))
    
    blocks = paragraphs_to_blocks(paragraphs)
    # Фильтруем пустые блоки и номера страниц
    filtered_blocks = []
    for b in blocks:
        block_text = b.get("text", "").strip()
        if not block_text:
            continue
        # Пропускаем номера страниц (маленькие числа внизу страницы - не метки глав!)
        if is_page_number(block_text):
            continue
        filtered_blocks.append(b)
    
    return filtered_blocks


def split_into_sections_by_size(blocks, max_size_kb=50):
    """Простое разделение блоков на секции по размеру (без поиска заголовков)"""
    sections = []
    current_blocks = []
    current_size = 0
    section_index = 1
    max_size = max_size_kb * 1024

    total_blocks = len(blocks)
    processed_count = 0

    for block in blocks:
        text = block.get("text", "").strip()
        # Пропускаем только полностью пустые блоки (БЕЗ фильтрации номеров страниц)
        if not text:
            continue
        
        block_size = len(text.encode("utf-8"))

        # Если текущая секция слишком большая - создаем новую
        if current_blocks and current_size + block_size > max_size:
            if current_blocks:
                sections.append({"title": f"Часть {section_index}", "blocks": current_blocks})
                section_index += 1
                current_blocks = []
                current_size = 0

        # Добавляем блок в текущую секцию
        current_blocks.append(block)
        current_size += block_size
        processed_count += 1

    # Сохраняем последнюю секцию (ВАЖНО: не забываем!)
    if current_blocks:
        sections.append({"title": f"Часть {section_index}", "blocks": current_blocks})

    # Отладочная информация
    total_blocks_in_sections = sum(len(s["blocks"]) for s in sections)
    if total_blocks != total_blocks_in_sections:
        print(f"⚠️  Предупреждение: обработано {total_blocks_in_sections} блоков из {total_blocks}")

    return sections


def split_into_chapters(blocks, max_size_kb=50):
    """Разбить блоки на главы просто по размеру (без поиска заголовков)"""
    chapters = []
    current_blocks = []
    current_size = 0
    chapter_index = 1
    max_size = max_size_kb * 1024

    # Просто делим все блоки на части по размеру
    for block in blocks:
        text = block.get("text", "").strip()
        # Пропускаем только действительно пустые блоки
        if not text:
            continue
        
        block_size = len(text.encode("utf-8"))

        # Если текущая глава слишком большая - создаем новую
        if current_blocks and current_size + block_size > max_size:
            if current_blocks:
                chapters.append({"title": f"Часть {chapter_index}", "blocks": current_blocks})
                chapter_index += 1
                current_blocks = []
                current_size = 0

        # Добавляем блок в текущую главу
        current_blocks.append(block)
        current_size += block_size

    # Сохраняем последнюю главу
    if current_blocks:
        chapters.append({"title": f"Часть {chapter_index}", "blocks": current_blocks})
    
    return chapters


def generate_cover_image(
    title: str,
    author: str = "",
    width: int = 1200,
    height: int = 1600,
    cover_colors: list[str] | None = None,
) -> bytes:
    """Сгенерировать обложку: верхний блок + полоска и нижняя градиентная зона.

    Если передана палитра, приняты пять HEX-цветов: полоска, верхний блок, заголовок,
    начало и конец градиента нижней зоны. Авторский текст подбирается автоматически."""
    if not HAS_PIL:
        raise ImportError("Pillow (PIL) не установлен. Установите: pip install Pillow")

    import random
    import colorsys
    import math

    rand = random.Random()

    def darken(color, ratio=0.35):
        return tuple(max(0, int(color[i] * ratio)) for i in range(3))

    def random_color():
        hue = rand.random()
        sat = rand.uniform(0.35, 0.8)
        val = rand.uniform(0.4, 0.95)
        return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, val))
    
    def brightness(rgb):
        """Вычисляет яркость цвета (0-255)"""
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    
    def relative_luminance(rgb):
        """Вычисляет относительную яркость по WCAG (0-1)"""
        def linearize(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = rgb
        return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)
    
    def contrast_ratio(color1, color2):
        """Вычисляет коэффициент контрастности по WCAG (1-21)"""
        l1 = relative_luminance(color1)
        l2 = relative_luminance(color2)
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)
    
    def rgb_to_hsv(rgb):
        """Преобразует RGB в HSV"""
        r, g, b = [c / 255.0 for c in rgb]
        return colorsys.rgb_to_hsv(r, g, b)
    
    def hsv_to_rgb(hsv):
        """Преобразует HSV в RGB"""
        h, s, v = hsv
        rgb = colorsys.hsv_to_rgb(h, s, v)
        return tuple(int(c * 255) for c in rgb)
    
    def complementary_color(rgb):
        """Возвращает противоположный цвет на цветовом круге"""
        h, s, v = rgb_to_hsv(rgb)
        # Противоположный цвет: поворот на 180 градусов (0.5 в нормализованном виде)
        comp_h = (h + 0.5) % 1.0
        # Сохраняем насыщенность и яркость, но инвертируем яркость для лучшего контраста
        comp_v = 1.0 - v if v > 0.5 else 1.0
        comp_s = min(1.0, s * 1.2)  # Немного увеличиваем насыщенность
        return hsv_to_rgb((comp_h, comp_s, comp_v))
    
    def ensure_contrast(text_color, bg_color, min_contrast=4.5):
        """
        Обеспечивает минимальный контраст между текстом и фоном.
        Возвращает цвет текста с достаточным контрастом.
        """
        current_contrast = contrast_ratio(text_color, bg_color)
        
        if current_contrast >= min_contrast:
            return text_color
        
        # Если контраст недостаточен, корректируем цвет
        bg_brightness = brightness(bg_color)
        
        # Если фон светлый, делаем текст темнее
        if bg_brightness > 128:
            # Темный текст на светлом фоне
            factor = 0.3  # Делаем очень темным
            new_color = tuple(int(c * factor) for c in text_color)
        else:
            # Светлый текст на темном фоне
            factor = 1.5  # Делаем светлее
            new_color = tuple(min(255, int(c * factor)) for c in text_color)
        
        # Проверяем контраст снова
        new_contrast = contrast_ratio(new_color, bg_color)
        
        # Если все еще недостаточно, используем максимальный контраст
        if new_contrast < min_contrast:
            if bg_brightness > 128:
                new_color = (0, 0, 0)  # Черный на светлом
            else:
                new_color = (255, 255, 255)  # Белый на темном
        
        return new_color

    custom_rgb = _coerce_cover_colors(cover_colors)
    if custom_rgb:
        stripe_color, top_block_color, title_color_hint, art_start_color, art_end_color = custom_rgb
        # Используем указанный цвет, но проверяем контраст
        title_color = title_color_hint
        # Если контраст недостаточен, используем противоположный цвет
        if contrast_ratio(title_color, top_block_color) < 4.5:
            title_color = complementary_color(top_block_color)
            title_color = ensure_contrast(title_color, top_block_color, min_contrast=4.5)
    else:
        art_start_color = random_color()
        art_end_color = random_color()
        top_block_color = random_color()
        stripe_color = darken(random_color())
        # Используем противоположный цвет на цветовом круге для заголовка
        title_color = complementary_color(top_block_color)
        # Обеспечиваем достаточный контраст
        title_color = ensure_contrast(title_color, top_block_color, min_contrast=4.5)

    def draw_gradient(target: Image.Image, start_color, end_color, orientation: str):
        tw, th = target.size
        for y in range(th):
            for x in range(tw):
                if orientation == "vertical":
                    ratio = y / max(1, th - 1)
                elif orientation == "horizontal":
                    ratio = x / max(1, tw - 1)
                elif orientation == "diagonal":
                    ratio = (x + y) / max(1, tw + th - 2)
                else:
                    cx = tw / 2
                    cy = th / 2
                    dist = math.hypot(x - cx, y - cy)
                    max_dist = math.hypot(cx, cy)
                    ratio = dist / max(1, max_dist)
                ratio = max(0.0, min(1.0, ratio))
                color = tuple(
                    int(start_color[i] + (end_color[i] - start_color[i]) * ratio)
                    for i in range(3)
                )
                target.putpixel((x, y), color)

    def contrast_text_color(bg_color, palette_color):
        """Старая функция для обратной совместимости"""
        if brightness(bg_color) > 180:
            return tuple(max(0, palette_color[i] - 110) for i in range(3))
        return tuple(min(255, palette_color[i] + 110) for i in range(3))

    def fix_hanging_prepositions(lines):
        hangers = {
            "в",
            "к",
            "с",
            "у",
            "о",
            "по",
            "из",
            "от",
            "до",
            "об",
            "на",
            "за",
            "над",
            "при",
            "про",
        }
        idx = 0
        while idx < len(lines) - 1:
            words = lines[idx].split()
            if words and words[-1].lower() in hangers:
                tail = words[-1]
                preceding = " ".join(words[:-1]).strip()
                lines[idx + 1] = f"{tail} {lines[idx + 1]}".strip()
                if preceding:
                    lines[idx] = preceding
                    idx += 1
                else:
                    lines.pop(idx)
                continue
            idx += 1
        return [ln for ln in lines if ln.strip()]

    top_block_height = int(height * 0.25)
    stripe_height = int(height * 0.08)
    art_top = top_block_height + stripe_height
    art_height = max(height - art_top, 0)

    base_orientation = rand.choice(["vertical", "horizontal", "diagonal", "radial"])
    img = Image.new("RGB", (width, height))
    
    # Рисуем градиент только в нижней части (после полоски)
    if art_height > 0:
        art_img = Image.new("RGB", (width, art_height))
        draw_gradient(art_img, art_start_color, art_end_color, base_orientation)
        img.paste(art_img, (0, art_top))

    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, top_block_height), fill=top_block_color)
    stripe_y0 = top_block_height
    stripe_y1 = stripe_y0 + stripe_height
    draw.rectangle((0, stripe_y0, width, stripe_y1), fill=stripe_color)

    logo_path = Path(__file__).resolve().parent / "logo.png"
    if logo_path.exists():
        try:
            with Image.open(logo_path) as logo_img:
                logo = logo_img.convert("RGBA")
                max_logo_height = max(24, stripe_height - 30)
                scale = min(
                    max_logo_height / logo.height,
                    (width * 0.25) / logo.width,
                    1,
                )
                if scale > 0:
                    new_size = (
                        max(1, int(logo.width * scale)),
                        max(1, int(logo.height * scale)),
                    )
                    logo = logo.resize(new_size, Image.LANCZOS)
                    logo_x = max(0, (width - new_size[0]) // 2)
                    logo_y = stripe_y0 + (stripe_height - new_size[1]) // 2
                    img.paste(logo, (logo_x, logo_y), logo)
        except Exception:
            pass

    try:
        title_font = ImageFont.truetype("arial.ttf", 72)
        author_font = ImageFont.truetype("arial.ttf", 52)
    except Exception:
        try:
            title_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 72)
            author_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 52)
        except Exception:
            title_font = ImageFont.load_default()
            author_font = ImageFont.load_default()

    if title_color:
        title_fill = title_color
        # Финальная проверка контраста
        title_fill = ensure_contrast(title_fill, top_block_color, min_contrast=4.5)
    else:
        # Если цвет не задан, используем противоположный цвет
        title_fill = complementary_color(top_block_color)
        title_fill = ensure_contrast(title_fill, top_block_color, min_contrast=4.5)
    
    # Цвет автора: максимальный контраст с фоном
    author_fill = ensure_contrast((255, 255, 255), top_block_color, min_contrast=4.5)
    # Если белый не дает достаточного контраста, используем черный
    if contrast_ratio(author_fill, top_block_color) < 4.5:
        author_fill = ensure_contrast((0, 0, 0), top_block_color, min_contrast=4.5)

    max_title_width = width - 160
    title_lines = []
    current_line = ""
    for word in title.split():
        test_line = current_line + (" " if current_line else "") + word
        bbox = draw.textbbox((0, 0), test_line, font=title_font)
        if bbox[2] - bbox[0] <= max_title_width:
            current_line = test_line
        else:
            if current_line:
                title_lines.append(current_line)
            current_line = word
    if current_line:
        title_lines.append(current_line)
    title_lines = fix_hanging_prepositions(title_lines)

    line_height = title_font.size + 12
    title_block_height = len(title_lines) * line_height

    if author:
        author_bbox = draw.textbbox((0, 0), author, font=author_font)
        author_height = author_bbox[3] - author_bbox[1]
        author_width = author_bbox[2] - author_bbox[0]
    else:
        author_height = 0
        author_width = 0

    spacing_between = title_font.size if title_lines and author else 0
    total_text_height = author_height
    if title_lines:
        if author:
            total_text_height += spacing_between
        total_text_height += title_block_height
    text_start_y = max(20, (top_block_height - total_text_height) // 2)

    current_y = text_start_y
    if author:
        author_x = (width - author_width) // 2 if author_width else 0
        draw.text((author_x, current_y), author, font=author_font, fill=author_fill)
        current_y += author_height + spacing_between

    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        draw.text((x, current_y), line, font=title_font, fill=title_fill)
        current_y += line_height

    from io import BytesIO
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG", quality=90)
    return img_bytes.getvalue()


def create_xhtml_section(blocks, title, css_href="../Styles/Style0001.css"):
    """Создать XHTML файл для раздела - БЕЗ фильтрации, добавляем все блоки"""
    body_parts = []
    blocks_added = 0
    blocks_skipped = 0
    
    for block in blocks:
        text = block.get("text", "").strip()
        # Пропускаем только полностью пустые блоки
        if not text:
            blocks_skipped += 1
            continue
        
        text_escaped = hesc(text)
        if block.get("role") == "heading":
            body_parts.append(f"<h2>{text_escaped}</h2>")
        else:
            body_parts.append(f"<p>{text_escaped}</p>")
        blocks_added += 1
    
    # Отладочная информация
    if blocks_skipped > 0 or blocks_added != len(blocks):
        print(f"   create_xhtml_section '{title[:30]}...': добавлено {blocks_added} блоков, пропущено {blocks_skipped} из {len(blocks)}")
    
    xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>

<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
 <title>{hesc(title)}</title>
 <link href="{css_href}" rel="stylesheet" type="text/css"/>
</head>

<body>
{chr(10).join(body_parts)}
</body>
</html>'''
    
    return xhtml


def update_content_opf(opf_content: str, section_files: list, title: str, author: str = "", has_cover: bool = False, sections: list = None):
    """Обновить content.opf с новыми разделами"""
    # Парсим XML
    root = ET.fromstring(opf_content)
    
    # Определяем namespace из корневого элемента
    opf_ns = 'http://www.idpf.org/2007/opf'
    dc_ns = 'http://purl.org/dc/elements/1.1/'
    dcterms_ns = 'http://purl.org/dc/terms/'
    
    # Находим namespace префиксы из корневого элемента
    ns_map = {}
    if root.tag.startswith('{'):
        # Извлекаем namespace из тега
        ns_uri = root.tag[1:].split('}')[0]
        ns_map[''] = ns_uri
    
    # Используем полные namespace URI для поиска
    ns = {'opf': opf_ns, 'dc': dc_ns}
    
    # Обновляем заголовок
    title_elem = root.find(f'.//{{{dc_ns}}}title')
    if title_elem is not None:
        title_elem.text = title
    
    # Обновляем или добавляем автора
    if author:
        metadata = root.find(f'.//{{{opf_ns}}}metadata')
        if metadata is not None:
            # Ищем существующего автора
            creator_elem = metadata.find(f'.//{{{dc_ns}}}creator')
            if creator_elem is not None:
                creator_elem.text = author
            else:
                # Создаем нового автора
                creator = ET.SubElement(metadata, f'{{{dc_ns}}}creator')
                creator.set('id', 'cre')
                creator.text = author
                # Добавляем meta для роли
                meta_role = ET.SubElement(metadata, f'{{{opf_ns}}}meta')
                meta_role.set('refines', '#cre')
                meta_role.set('property', 'role')
                meta_role.set('scheme', 'marc:relators')
                meta_role.text = 'aut'
    
    # Обновляем дату модификации
    # Ищем meta с property="dcterms:modified"
    modified_elem = None
    for meta in root.findall(f'.//{{{opf_ns}}}meta'):
        if meta.get('property') == 'dcterms:modified':
            modified_elem = meta
            break
    
    if modified_elem is None:
        # Создаем новый meta элемент
        metadata = root.find(f'.//{{{opf_ns}}}metadata')
        if metadata is not None:
            meta = ET.SubElement(metadata, f'{{{opf_ns}}}meta')
            meta.set('property', 'dcterms:modified')
            modified_elem = meta
    
    if modified_elem is not None:
        # Используем timezone-aware datetime вместо устаревшего utcnow()
        from datetime import timezone
        # В EPUB 3 атрибут content должен быть текстовым содержимым, а не атрибутом
        modified_elem.text = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        # Удаляем атрибут content, если он был установлен неправильно
        if 'content' in modified_elem.attrib:
            del modified_elem.attrib['content']
    
    # Обновляем identifier (генерируем новый UUID и возвращаем его для использования в NCX)
    book_uuid = None
    identifier = root.find(f'.//{{{dc_ns}}}identifier[@id="BookId"]')
    if identifier is not None:
        book_uuid = str(uuid.uuid4())
        identifier.text = f'urn:uuid:{book_uuid}'
    
    # Обновляем обложку в manifest, если создана новая
    if has_cover:
        manifest = root.find(f'.//{{{opf_ns}}}manifest')
        if manifest is not None:
            # Удаляем старую обложку, если есть
            for item in list(manifest):
                href = item.get('href', '')
                if 'cover' in href.lower() and href.endswith(('.jpg', '.jpeg', '.png')):
                    manifest.remove(item)
            
            # Добавляем новую обложку
            cover_item = ET.SubElement(manifest, f'{{{opf_ns}}}item')
            cover_item.set('id', 'cover-image')
            cover_item.set('href', 'Images/cover.jpg')
            cover_item.set('media-type', 'image/jpeg')
            cover_item.set('properties', 'cover-image')
    
    # Находим manifest и spine
    manifest = root.find(f'.//{{{opf_ns}}}manifest')
    spine = root.find(f'.//{{{opf_ns}}}spine')
    
    if manifest is None or spine is None:
        return opf_content  # Не удалось найти, возвращаем как есть
    
    # Удаляем старые Section и Chapter файлы из manifest и spine
    for item in list(manifest):
        href = item.get('href', '')
        # Удаляем как старые Section*.xhtml, так и старые Chapter*.xhtml
        if (href.startswith('Text/Section') or href.startswith('Text/Chapter')) and href.endswith('.xhtml'):
            manifest.remove(item)
    
    for itemref in list(spine):
        idref = itemref.get('idref', '')
        # Удаляем как старые Section, так и старые Chapter
        if idref.startswith('Section') or idref.startswith('Chapter'):
            spine.remove(itemref)
    
    # Находим позицию для вставки разделов (после последнего не-Section элемента)
    insert_pos = len(spine)
    for idx, itemref in enumerate(spine):
        idref = itemref.get('idref', '')
        if idref.startswith('Chapter'):
            insert_pos = idx
            break
    
    # Добавляем новые разделы в manifest и spine (в правильном порядке)
    added_to_manifest = 0
    added_to_spine = 0
    
    for i, section_file in enumerate(section_files, 1):
        # section_file уже содержит имя файла типа "Chapter0001.xhtml"
        section_id = section_file  # Используем имя файла как есть
        item_id = f"Chapter{i:04d}"
        href = f"Text/{section_id}"
        
        # Добавляем в manifest
        item = ET.SubElement(manifest, f'{{{opf_ns}}}item')
        item.set('id', item_id)
        item.set('href', href)
        item.set('media-type', 'application/xhtml+xml')
        added_to_manifest += 1
        
        # Добавляем в spine в правильном порядке (последовательно)
        itemref = ET.Element(f'{{{opf_ns}}}itemref')
        itemref.set('idref', item_id)
        spine.insert(insert_pos + i - 1, itemref)
        added_to_spine += 1
    
    # Отладочная информация
    print(f"   Добавлено в manifest: {added_to_manifest} глав")
    print(f"   Добавлено в spine: {added_to_spine} глав")
    
    # Обновляем guide для обложки
    if has_cover:
        guide = root.find(f'.//{{{opf_ns}}}guide')
        if guide is None:
            guide = ET.SubElement(root, f'{{{opf_ns}}}guide')
        
        # Удаляем старую ссылку на обложку
        for ref in list(guide):
            if ref.get('type') == 'cover':
                guide.remove(ref)
        
        # Добавляем новую ссылку на обложку
        cover_ref = ET.SubElement(guide, f'{{{opf_ns}}}reference')
        cover_ref.set('type', 'cover')
        cover_ref.set('title', 'Обложка')
        cover_ref.set('href', 'Text/cover.xhtml')
    
    # Преобразуем обратно в строку
    # Сохраняем исходные namespace префиксы
    ET.register_namespace('', opf_ns)
    ET.register_namespace('dc', dc_ns)
    ET.register_namespace('dcterms', dcterms_ns)
    
    xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
    # Исправляем форматирование для соответствия оригиналу
    xml_str = xml_str.replace(' />', '/>')
    return xml_str, book_uuid


def generate_epub(
    template_epub: Path,
    blocks: list,
    output_epub: Path,
    title: str,
    author: str = "",
    cover_colors: list[str] | None = None,
    max_chapter_size_kb: int = 50,
    use_chapter_heads: bool = False,
):
    """Генерировать EPUB на основе шаблона и блоков текста
    
    Args:
        use_chapter_heads: Если True, использует поиск заголовков для разделения на главы.
                          Если False, просто разбивает по размеру на секции.
    """
    
    # Разбиваем на разделы
    print(f"\n📖 Разбиение на части:")
    print(f"   Входных блоков: {len(blocks)}")
    
    if use_chapter_heads:
        sections = split_into_chapters(blocks, max_size_kb=max_chapter_size_kb)
        total_blocks_in_sections = sum(len(s["blocks"]) for s in sections)
        print(f"   Разбито на {len(sections)} глав по заголовкам (макс. {max_chapter_size_kb} KB)")
        print(f"   Блоков в главах: {total_blocks_in_sections} из {len(blocks)}")
        if total_blocks_in_sections != len(blocks):
            lost = len(blocks) - total_blocks_in_sections
            print(f"   ⚠️  ВНИМАНИЕ: потеряно {lost} блоков ({lost*100/len(blocks):.1f}%) при разбиении!")
    else:
        sections = split_into_sections_by_size(blocks, max_size_kb=max_chapter_size_kb)
        total_blocks_in_sections = sum(len(s["blocks"]) for s in sections)
        print(f"   Разбито на {len(sections)} частей по размеру (макс. {max_chapter_size_kb} KB)")
        print(f"   Блоков в частях: {total_blocks_in_sections} из {len(blocks)}")
        if total_blocks_in_sections != len(blocks):
            lost = len(blocks) - total_blocks_in_sections
            print(f"   ⚠️  ВНИМАНИЕ: потеряно {lost} блоков ({lost*100/len(blocks):.1f}%) при разбиении!")
        else:
            print(f"   ✓ Все блоки попали в части!")
    
    # Создаем временную директорию
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Распаковываем шаблон EPUB
        with zipfile.ZipFile(template_epub, 'r') as z:
            z.extractall(tmp_path)
        
        oebps_path = tmp_path / "OEBPS"
        text_path = oebps_path / "Text"
        images_path = oebps_path / "Images"
        
        # Генерируем обложку
        has_cover_image = False
        if HAS_PIL:
            try:
                cover_image_data = generate_cover_image(
                    title,
                    author=author,
                    cover_colors=cover_colors,
                )
                cover_image_path = images_path / "cover.jpg"
                cover_image_path.write_bytes(cover_image_data)
                has_cover_image = True
                print(f"Обложка создана: {cover_image_path}")
                
                # Обновляем cover.xhtml для использования новой обложки
                cover_xhtml_path = text_path / "cover.xhtml"
                if cover_xhtml_path.exists():
                    cover_xhtml_content = cover_xhtml_path.read_text(encoding="utf-8")
                    # Обновляем заголовок
                    cover_xhtml_content = re.sub(
                        r'<title>.*?</title>',
                        f'<title>{hesc(title)}</title>',
                        cover_xhtml_content,
                        flags=re.DOTALL
                    )
                    # Удаляем старый img тег, если есть (оставляем только SVG)
                    cover_xhtml_content = re.sub(
                        r'<img[^>]*src="[^"]*cover[^"]*"[^>]*/>',
                        '',
                        cover_xhtml_content,
                        flags=re.IGNORECASE
                    )
                    # Обновляем ссылку на изображение в SVG
                    cover_xhtml_content = re.sub(
                        r'<image[^>]*xlink:href="[^"]*"[^>]*>',
                        '<image width="665" height="1000" xlink:href="../Images/cover.jpg"/>',
                        cover_xhtml_content,
                        flags=re.IGNORECASE
                    )
                    cover_xhtml_path.write_text(cover_xhtml_content, encoding="utf-8")
            except Exception as e:
                print(f"Предупреждение: не удалось создать обложку: {e}")
        else:
            print("Предупреждение: Pillow не установлен, обложка не будет создана")
        
        # Удаляем старые Section и Chapter файлы
        for old_section in text_path.glob("Section*.xhtml"):
            old_section.unlink()
        for old_chapter in text_path.glob("Chapter*.xhtml"):
            old_chapter.unlink()
        
        # Читаем content.opf
        opf_path = oebps_path / "content.opf"
        opf_content = opf_path.read_text(encoding="utf-8")
        
        # Генерируем новые разделы
        section_files = []
        total_blocks_before_filter = sum(len(ch.get("blocks", [])) for ch in sections)
        total_filtered_blocks = 0
        
        for i, chapter in enumerate(sections, 1):
            section_blocks = chapter.get("blocks", [])
            original_count = len(section_blocks)
            
            # НЕ фильтруем блоки - добавляем все как есть (только пустые пропускаем)
            filtered_blocks = []
            for block in section_blocks:
                text = block.get("text", "").strip()
                if text:  # Добавляем все непустые блоки без фильтрации
                    filtered_blocks.append(block)
            
            total_filtered_blocks += len(filtered_blocks)
            
            # Пропускаем только полностью пустые главы
            if not filtered_blocks:
                print(f"⚠️  Пропущена пустая глава {i}: {chapter.get('title', 'Без названия')}")
                continue
            
            section_id = f"Chapter{i:04d}.xhtml"
            section_title = chapter.get("title") or title
            
            # Проверяем размер XHTML перед записью
            xhtml_content = create_xhtml_section(filtered_blocks, section_title)
            xhtml_size = len(xhtml_content.encode("utf-8"))
            
            section_file = text_path / section_id
            section_file.write_text(xhtml_content, encoding="utf-8")
            section_files.append(section_id)
            
            # Отладочная информация для каждой главы
            if i <= 5 or i == len(sections) or (i % 10 == 0):
                first_block_text = filtered_blocks[0].get("text", "")[:50] if filtered_blocks else ""
                last_block_text = filtered_blocks[-1].get("text", "")[:50] if filtered_blocks else ""
                print(f"   Глава {i}/{len(sections)} '{section_title[:30]}...': {len(filtered_blocks)} блоков, XHTML: {xhtml_size/1024:.1f} KB")
                if i <= 3:
                    print(f"      Первый блок: {repr(first_block_text)}")
                    print(f"      Последний блок: {repr(last_block_text)}")
        
        # Итоговая отладочная информация
        print(f"\n📊 Статистика генерации EPUB:")
        print(f"   Всего секций: {len(sections)}")
        print(f"   Блоков в секциях: {total_filtered_blocks} из {total_blocks_before_filter}")
        if total_blocks_before_filter != total_filtered_blocks:
            lost = total_blocks_before_filter - total_filtered_blocks
            print(f"   ⚠️  ВНИМАНИЕ: потеряно {lost} блоков ({lost*100/total_blocks_before_filter:.1f}%)!")
        else:
            print(f"   ✓ Все блоки сохранены!")
        print(f"   Создано XHTML файлов: {len(section_files)}")
        
        # Проверяем размеры всех XHTML файлов
        total_xhtml_size = 0
        for section_file in section_files:
            file_path = text_path / section_file
            if file_path.exists():
                file_size = file_path.stat().st_size
                total_xhtml_size += file_size
        print(f"   Общий размер всех XHTML файлов: {total_xhtml_size/1024:.1f} KB")
        
        # Обновляем титульную страницу
        titul_path = text_path / "Titul.xhtml"
        if titul_path.exists():
            titul_content = titul_path.read_text(encoding="utf-8")
            # Обновляем заголовок и автора
            titul_content = re.sub(r'<title>.*?</title>', f'<title>{hesc(title)}</title>', titul_content, flags=re.DOTALL)
            titul_content = re.sub(r'<h1>.*?</h1>', f'<h1>{hesc(title)}</h1>', titul_content, flags=re.DOTALL)
            if author:
                titul_content = re.sub(r'<p class="author">.*?</p>', f'<p class="author">{hesc(author)}</p>', titul_content, flags=re.DOTALL)
            titul_path.write_text(titul_content, encoding="utf-8")
        
        # Обновляем оглавление (toc.ncx)
        toc_path = oebps_path / "toc.ncx"
        if toc_path.exists():
            toc_content = toc_path.read_text(encoding="utf-8")
            toc_root = ET.fromstring(toc_content)
            ncx_ns = 'http://www.daisy.org/z3986/2005/ncx/'
            ncx_ns_map = {'ncx': ncx_ns}
            
            # Обновляем заголовок
            doc_title_elem = toc_root.find('.//ncx:docTitle', ncx_ns_map)
            if doc_title_elem is not None:
                doc_title_text = doc_title_elem.find('ncx:text', ncx_ns_map)
                if doc_title_text is not None:
                    doc_title_text.text = title
            
            # Обновляем идентификатор NCX, чтобы он совпадал с OPF
            # Сначала получим UUID из OPF (будет передан позже)
            # Пока просто найдем элемент head и обновим его позже
            
            # Обновляем navMap - удаляем старые разделы и добавляем новые
            nav_map = toc_root.find('.//ncx:navMap', ncx_ns_map)
            if nav_map is not None:
                # Удаляем ВСЕ старые navPoint (и Section, и Chapter)
                # Сохраняем только корневой navPoint для Titul, если он есть
                titul_nav_point = None
                for nav_point in list(nav_map):
                    content = nav_point.find('ncx:content', ncx_ns_map)
                    if content is not None:
                        src = content.get('src', '')
                        # Сохраняем ссылку на Titul, если она есть
                        if 'Titul.xhtml' in src:
                            # Удаляем все вложенные navPoint (Section, Chapter и т.д.)
                            # Используем findall с рекурсивным поиском, но исключаем сам nav_point
                            nested_points = nav_point.findall('.//ncx:navPoint', ncx_ns_map)
                            # Удаляем в обратном порядке, чтобы не нарушить структуру при итерации
                            for nested_point in reversed(nested_points):
                                # Пропускаем сам nav_point (корневой Titul)
                                if nested_point != nav_point:
                                    # Удаляем через родителя
                                    parent = nested_point.getparent()
                                    if parent is not None:
                                        parent.remove(nested_point)
                            titul_nav_point = nav_point
                        elif 'Section' in src or 'Chapter' in src:
                            # Удаляем старые Section и Chapter на верхнем уровне
                            nav_map.remove(nav_point)
                
                # Если есть Titul navPoint, добавляем главы внутрь него
                # Иначе создаем отдельные navPoint для каждой главы
                if titul_nav_point is not None:
                    # Добавляем главы внутрь Titul navPoint
                    # playOrder начинаем с 2, потому что navPoint1 это Titul
                    start_order = 2
                    for i, (section_file, chapter) in enumerate(zip(section_files, sections), 1):
                        section_id = f"Chapter{i:04d}.xhtml"
                        section_title = chapter.get("title", title)
                        
                        nav_point = ET.SubElement(titul_nav_point, f'{{{ncx_ns}}}navPoint')
                        nav_point.set('id', f'navPoint{i+start_order}')
                        nav_point.set('playOrder', str(i+1))
                        
                        nav_label = ET.SubElement(nav_point, f'{{{ncx_ns}}}navLabel')
                        nav_label_text = ET.SubElement(nav_label, f'{{{ncx_ns}}}text')
                        nav_label_text.text = section_title
                        
                        nav_content = ET.SubElement(nav_point, f'{{{ncx_ns}}}content')
                        nav_content.set('src', f'Text/{section_id}')
                else:
                    # Добавляем главы как отдельные navPoint на верхнем уровне
                    for i, (section_file, chapter) in enumerate(zip(section_files, sections), 1):
                        section_id = f"Chapter{i:04d}.xhtml"
                        section_title = chapter.get("title", title)
                        
                        nav_point = ET.SubElement(nav_map, f'{{{ncx_ns}}}navPoint')
                        nav_point.set('id', f'navPoint{i+1}')
                        nav_point.set('playOrder', str(i+1))
                        
                        nav_label = ET.SubElement(nav_point, f'{{{ncx_ns}}}navLabel')
                        nav_label_text = ET.SubElement(nav_label, f'{{{ncx_ns}}}text')
                        nav_label_text.text = section_title
                        
                        nav_content = ET.SubElement(nav_point, f'{{{ncx_ns}}}content')
                        nav_content.set('src', f'Text/{section_id}')
            
            ET.register_namespace('', ncx_ns)
            toc_xml = ET.tostring(toc_root, encoding='utf-8', xml_declaration=True).decode('utf-8')
            toc_path.write_text(toc_xml, encoding="utf-8")
        
        # Обновляем nav.xhtml (EPUB 3 навигация) используя XML парсер для сохранения структуры
        nav_xhtml_path = text_path / "nav.xhtml"
        if nav_xhtml_path.exists():
            try:
                # Парсим XML для безопасного обновления
                nav_tree = ET.parse(nav_xhtml_path)
                nav_root = nav_tree.getroot()
                
                # Обновляем заголовок
                title_elem = nav_root.find('.//{http://www.w3.org/1999/xhtml}title')
                if title_elem is not None:
                    title_elem.text = title
                
                # Находим nav с epub:type="toc"
                xhtml_ns = 'http://www.w3.org/1999/xhtml'
                epub_ns = 'http://www.idpf.org/2007/ops'
                
                # Ищем nav элемент с epub:type="toc"
                toc_nav = None
                for nav in nav_root.findall('.//{http://www.w3.org/1999/xhtml}nav'):
                    if nav.get(f'{{{epub_ns}}}type') == 'toc' or nav.get('epub:type') == 'toc':
                        toc_nav = nav
                        break
                
                if toc_nav is not None:
                    # Находим <ol> внутри nav
                    ol_elem = toc_nav.find('.//{http://www.w3.org/1999/xhtml}ol')
                    if ol_elem is not None:
                        # Удаляем старые ссылки на Section и Chapter
                        for li in list(ol_elem):
                            # Проверяем все ссылки внутри <li>
                            links = li.findall('.//{http://www.w3.org/1999/xhtml}a')
                            should_remove = False
                            for link in links:
                                href = link.get('href', '')
                                if 'Section' in href or 'Chapter' in href:
                                    should_remove = True
                                    break
                            
                            # Если это Titul с вложенным списком, удаляем только вложенный список
                            titul_link = li.find('.//{http://www.w3.org/1999/xhtml}a[@href="Titul.xhtml"]')
                            if titul_link is not None:
                                # Удаляем вложенный <ol> внутри этого <li>
                                nested_ol = li.find('.//{http://www.w3.org/1999/xhtml}ol')
                                if nested_ol is not None:
                                    li.remove(nested_ol)
                            elif should_remove:
                                # Удаляем весь <li> если он содержит Section или Chapter
                                ol_elem.remove(li)
                        
                        # Находим <li> с Titul (если есть) для добавления глав внутрь
                        titul_li = None
                        for li in ol_elem:
                            titul_link = li.find('.//{http://www.w3.org/1999/xhtml}a[@href="Titul.xhtml"]')
                            if titul_link is not None:
                                titul_li = li
                                break
                        
                        # Создаем новые ссылки на главы
                        if titul_li is not None:
                            # Добавляем вложенный <ol> внутрь Titul <li>
                            nested_ol = ET.SubElement(titul_li, f'{{{xhtml_ns}}}ol')
                            for i, (section_file, chapter) in enumerate(zip(section_files, sections), 1):
                                section_id = f"Chapter{i:04d}.xhtml"
                                section_title = chapter.get("title", title)
                                
                                li = ET.SubElement(nested_ol, f'{{{xhtml_ns}}}li')
                                a = ET.SubElement(li, f'{{{xhtml_ns}}}a')
                                a.set('href', section_id)
                                a.text = section_title
                        else:
                            # Добавляем главы как отдельные <li> на верхнем уровне
                            for i, (section_file, chapter) in enumerate(zip(section_files, sections), 1):
                                section_id = f"Chapter{i:04d}.xhtml"
                                section_title = chapter.get("title", title)
                                
                                li = ET.SubElement(ol_elem, f'{{{xhtml_ns}}}li')
                                a = ET.SubElement(li, f'{{{xhtml_ns}}}a')
                                a.set('href', section_id)
                                a.text = section_title
                        
                        # Сохраняем обновленный файл
                        ET.register_namespace('', xhtml_ns)
                        ET.register_namespace('epub', epub_ns)
                        nav_tree.write(nav_xhtml_path, encoding='utf-8', xml_declaration=True)
                        print(f"   Обновлен nav.xhtml: добавлено {len(section_files)} ссылок на главы")
            except Exception as e:
                print(f"⚠️  Предупреждение: не удалось обновить nav.xhtml через XML парсер: {e}")
                print(f"   Используется fallback метод через регулярные выражения")
                # Fallback на старый метод через регулярные выражения
                nav_content = nav_xhtml_path.read_text(encoding="utf-8")
                nav_content = re.sub(
                    r'<title>.*?</title>',
                    f'<title>{hesc(title)}</title>',
                    nav_content,
                    flags=re.DOTALL
                )
                # Простое удаление старых ссылок и добавление новых
                nav_content = re.sub(
                    r'<ol>.*?</ol>',
                    lambda m: f'<ol>\n' + '\n'.join([f'      <li><a href="Chapter{i:04d}.xhtml">{hesc(sections[i-1].get("title", title))}</a></li>' for i in range(1, len(section_files)+1)]) + '\n    </ol>',
                    nav_content,
                    count=1,
                    flags=re.DOTALL | re.IGNORECASE
                )
                nav_xhtml_path.write_text(nav_content, encoding="utf-8")
        
        # Обновляем content.opf (с обложкой, если создана)
        updated_opf, book_uuid = update_content_opf(opf_content, section_files, title, author, has_cover_image, sections)
        opf_path.write_text(updated_opf, encoding="utf-8")
        
        # Обновляем идентификатор в NCX, чтобы он совпадал с OPF
        if toc_path.exists() and book_uuid:
            toc_content = toc_path.read_text(encoding="utf-8")
            toc_root = ET.fromstring(toc_content)
            ncx_ns = 'http://www.daisy.org/z3986/2005/ncx/'
            ncx_ns_map = {'ncx': ncx_ns}
            
            # Обновляем идентификатор в head
            head_elem = toc_root.find('.//ncx:head', ncx_ns_map)
            if head_elem is not None:
                # Ищем или создаем meta с name="dtb:uid"
                uid_meta = None
                for meta in head_elem.findall('ncx:meta', ncx_ns_map):
                    if meta.get('name') == 'dtb:uid':
                        uid_meta = meta
                        break
                
                if uid_meta is None:
                    uid_meta = ET.SubElement(head_elem, f'{{{ncx_ns}}}meta')
                    uid_meta.set('name', 'dtb:uid')
                
                uid_meta.set('content', f'urn:uuid:{book_uuid}')
            
            ET.register_namespace('', ncx_ns)
            toc_xml = ET.tostring(toc_root, encoding='utf-8', xml_declaration=True).decode('utf-8')
            toc_path.write_text(toc_xml, encoding="utf-8")
        
        # Проверяем, что все Chapter файлы существуют перед сборкой EPUB
        missing_files = []
        for section_file in section_files:
            file_path = text_path / section_file
            if not file_path.exists():
                missing_files.append(section_file)
        
        if missing_files:
            print(f"⚠️  ВНИМАНИЕ: не найдены файлы глав: {missing_files}")
        
        # Собираем новый EPUB
        with zipfile.ZipFile(output_epub, 'w', zipfile.ZIP_DEFLATED) as z:
            # mimetype должен быть первым и без сжатия
            mimetype_path = tmp_path / "mimetype"
            if mimetype_path.exists():
                z.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
            
            # Остальные файлы
            files_added = 0
            chapter_files_added = 0
            for file_path in tmp_path.rglob("*"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(tmp_path)
                    if rel_path.name != "mimetype":  # mimetype уже добавлен
                        z.write(file_path, rel_path)
                        files_added += 1
                        if "Chapter" in rel_path.name:
                            chapter_files_added += 1
            
            print(f"   Добавлено в EPUB: {files_added} файлов, из них {chapter_files_added} глав")
        
        print(f"EPUB создан: {output_epub}")


def main():
    ap = argparse.ArgumentParser(
        description="Генерация EPUB на основе шаблона и текста из JSON, HTML или TXT"
    )
    ap.add_argument("--template", required=True, help="Путь к шаблону EPUB")
    ap.add_argument("--in", dest="inp", required=True, help="Входной файл: JSON (structured.json), HTML или TXT")
    ap.add_argument("--out", required=True, help="Выходной EPUB файл")
    ap.add_argument("--title", required=True, help="Заголовок книги")
    ap.add_argument("--author", default="", help="Автор книги (для обложки)")
    ap.add_argument(
        "--cover-colors",
        default="",
        help="Пять HEX-цветов (полоска; верхний блок; заголовок; нижний градиент начало; конец)",
    )
    ap.add_argument("--max-chapter-size", type=int, default=50, help="Максимальный размер главы/секции в KB (по умолчанию 50)")
    ap.add_argument("--use-chapter-heads", action="store_true", help="Использовать поиск заголовков для разделения на главы (по умолчанию: простое разделение по размеру)")
    args = ap.parse_args()
    
    template_epub = Path(args.template)
    input_file = Path(args.inp)
    output_epub = Path(args.out)
    
    if not template_epub.exists():
        print(f"Ошибка: шаблон EPUB не найден: {template_epub}")
        return 1
    
    if not input_file.exists():
        print(f"Ошибка: входной файл не найден: {input_file}")
        return 1
    
    # Загружаем блоки
    suffix = input_file.suffix.lower()
    if suffix == ".json":
        blocks = load_blocks_from_json(input_file)
    elif suffix in (".html", ".htm"):
        blocks = load_blocks_from_html(input_file)
    elif suffix == ".txt":
        text_content = input_file.read_text(encoding="utf-8")
        blocks = load_blocks_from_text(text_content)
    else:
        print(f"Ошибка: неподдерживаемый формат входного файла: {input_file.suffix}")
        print("Поддерживаются: .json, .html, .htm")
        return 1
    
    if not blocks:
        print("Ошибка: не найдено блоков текста")
        return 1
    
    # Проверяем, сколько блоков имеют текст
    blocks_with_text = [b for b in blocks if b.get("text", "").strip()]
    print(f"Загружено {len(blocks)} блоков, из них {len(blocks_with_text)} с текстом")
    
    if len(blocks_with_text) == 0:
        print("⚠️  Предупреждение: все блоки пустые!")
        # Показываем первые несколько блоков для отладки
        print("Первые 5 блоков:")
        for i, b in enumerate(blocks[:5]):
            print(f"  Блок {i}: role={b.get('role')}, text={repr(b.get('text', '')[:50])}")
        return 1
    
    # Фильтруем пустые блоки
    blocks = blocks_with_text
    print(f"Используется {len(blocks)} блоков с текстом")
    
    # Показываем статистику по блокам для отладки
    total_text_length = sum(len(b.get("text", "").encode("utf-8")) for b in blocks)
    print(f"Общий размер текста: {total_text_length / 1024:.1f} KB")
    
    # Показываем первые и последние блоки для проверки
    if len(blocks) > 0:
        print(f"Первый блок: {repr(blocks[0].get('text', '')[:100])}")
        print(f"Последний блок: {repr(blocks[-1].get('text', '')[:100])}")
    
    # Подсчитываем блоки по типам
    heading_count = sum(1 for b in blocks if b.get("role") == "heading")
    paragraph_count = sum(1 for b in blocks if b.get("role") == "paragraph")
    print(f"Блоков-заголовков: {heading_count}, блоков-абзацев: {paragraph_count}")
    
    cover_colors = None
    if args.cover_colors:
        try:
            cover_colors = parse_cover_colors_arg(args.cover_colors)
        except ValueError as exc:
            ap.error(str(exc))

    # Генерируем EPUB
    generate_epub(
        template_epub,
        blocks,
        output_epub,
        args.title,
        args.author,
        cover_colors=cover_colors,
        max_chapter_size_kb=args.max_chapter_size,
        use_chapter_heads=args.use_chapter_heads,
    )
    
    return 0


if __name__ == "__main__":
    exit(main())

