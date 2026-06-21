# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional, Sequence


def is_valid_bbox(bbox: Sequence[float] | None) -> bool:
    if not bbox or len(bbox) < 4:
        return False
    x0, y0, x1, y1 = bbox[:4]
    if x1 <= x0 or y1 <= y0:
        return False
    if x0 == y0 == x1 == y1 == 0:
        return False
    return True


def union_bbox(a: Sequence[float], b: Sequence[float]) -> List[float]:
    if not is_valid_bbox(a):
        return list(b[:4])
    if not is_valid_bbox(b):
        return list(a[:4])
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def union_many(boxes: Sequence[Sequence[float]]) -> Optional[List[float]]:
    out: Optional[List[float]] = None
    for bb in boxes:
        if not is_valid_bbox(bb):
            continue
        out = union_bbox(out or bb, bb) if out else list(bb[:4])
    return out
