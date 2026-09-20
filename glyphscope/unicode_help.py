"""组合字符与 variation selector 的离线判定（不依赖 unicodedata 版本细节）。"""
from __future__ import annotations

VS_START = 0xFE00
VS_END = 0xFE0F
VS_SUP_START = 0xE0100
VS_SUP_END = 0xE01EF
ZWJ = 0x200D
ZWNJ = 0x200C


def is_variation_selector(cp):
    return (VS_START <= cp <= VS_END) or (
        VS_SUP_START <= cp <= VS_SUP_END)


# 常见通用组合范围（General_Category Mn/Mc/Sk 中的主要区块），
# 无需渲染，仅用于把组合基字+标记聚类展示。
_COMBINING_RANGES = [
    (0x0300, 0x036F),   # Combining Diacritical Marks
    (0x1AB0, 0x1AFF),
    (0x1DC0, 0x1DFF),
    (0x20D0, 0x20FF),
    (0xFE20, 0xFE2F),
    (0x16FE0, 0x16FE1),
    (0x1D167, 0x1D169),
    (0x1D17B, 0x1D182),
    (0x1D185, 0x1D18B),
    (0x1D1AA, 0x1D1AD),
    (0xE0100, 0xE01EF),
]


def is_combining_mark(cp):
    if is_variation_selector(cp):
        return False
    for start, end in _COMBINING_RANGES:
        if start <= cp <= end:
            return True
    return False


def codepoint_label(cp):
    if cp is None:
        return ""
    if cp > 0xFFFF:
        return "U+%05X" % cp
    return "U+%04X" % cp


def is_surrogate_codeunit_value(cp):
    return 0xD800 <= cp <= 0xDFFF
