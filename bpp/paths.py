# -*- coding: utf-8 -*-
"""Пути к встроенным assets и review-пакету (без внешнего ocr2epub)."""
from __future__ import annotations

import os
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent


def package_root() -> Path:
    return _PKG_ROOT


def default_rules_dir() -> Path:
    env = os.environ.get("OCR2EPUB_RULES") or os.environ.get("BPP_RULES")
    if env:
        return Path(env)
    return _PKG_ROOT / "assets" / "rules" / "ocr"


def default_oldspelling_path() -> Path:
    env = os.environ.get("OCR2EPUB_OLD_SPELLING") or os.environ.get("BPP_OLD_SPELLING")
    if env:
        return Path(env)
    return _PKG_ROOT / "assets" / "oldspelling.py"


def default_corrector_root() -> Path:
    env = os.environ.get("PDF_FAITHFUL_CORRECTOR_ROOT")
    if env:
        return Path(env)
    return _PKG_ROOT


def assets_dir() -> Path:
    return _PKG_ROOT / "assets"


def web_dir() -> Path:
    return _PKG_ROOT / "web"
