"""SFNT 表目录解析与表数据切片。"""
from __future__ import annotations

import struct

from .binary import Reader
from .errors import FontCorrupt

SFNT_TTF = b"\x00\x01\x00\x00"
SFNT_TTF_TYPO = b"true"
SFNT_TTF_TYPO2 = b"typ1"
SFNT_OTTO = b"OTTO"
SFNT_TTC = b"ttcf"
SFNT_WOFF = b"wOFF"
SFNT_WOFF2 = b"wOF2"
SUPPORTED_SCALERS = (SFNT_TTF, SFNT_TTF_TYPO, SFNT_TTF_TYPO2)


class TableRecord:
    __slots__ = ("tag", "checksum", "offset", "length", "data")

    def __init__(self, tag, checksum, offset, length, data):
        self.tag = tag
        self.checksum = checksum
        self.offset = offset
        self.length = length
        self.data = data


class SFNT:
    def __init__(self, raw, scaler, tables):
        self.raw = raw
        self.scaler = scaler
        self.tables = tables  # type: dict[str, TableRecord]

    def has(self, tag):
        return tag in self.tables

    def reader(self, tag):
        rec = self.tables[tag]
        return Reader(rec.data, 0, len(rec.data), table=tag)

    def require(self, tag):
        if tag not in self.tables:
            raise FontCorrupt("缺少必需表", tag)
        return self.reader(tag)


def parse_sfnt(raw):
    if len(raw) < 12:
        raise FontCorrupt("文件过短，不是合法 SFNT 字体")
    scaler = raw[:4]
    if scaler in (SFNT_WOFF, SFNT_WOFF2, SFNT_TTC):
        raise FontCorrupt("不支持的容器（仅支持未压缩 TTF）",
                          detail=scaler.decode("latin-1", "replace"))
    if scaler == SFNT_OTTO:
        from .errors import UnsupportedFont
        raise UnsupportedFont("CFF/OTF 轮廓暂不支持")
    if scaler not in SUPPORTED_SCALERS:
        raise FontCorrupt("无法识别的 sfntVersion",
                          detail=scaler.hex())

    num_tables = struct.unpack_from(">H", raw, 4)[0]
    if num_tables == 0:
        raise FontCorrupt("表目录为空")
    # searchRange/exactRange/rangeSelector 不强制校验，只做上限检查
    header_size = 12 + 16 * num_tables
    if header_size > len(raw):
        raise FontCorrupt("表目录越界")

    tables = {}
    pos = 12
    for i in range(num_tables):
        tag = raw[pos:pos + 4]
        try:
            tag_s = tag.decode("latin-1")
        except UnicodeDecodeError:
            raise FontCorrupt("非法表标签", detail=tag.hex())
        checksum, offset, length = struct.unpack_from(">III", raw, pos + 4)
        pos += 16
        if not _printable_tag(tag_s):
            raise FontCorrupt("非法表标签", tag_s)
        if length == 0 and tag_s not in ("DSIG", "glyf"):
            raise FontCorrupt("空表", tag_s)
        if offset > len(raw) or length > len(raw) - offset:
            raise FontCorrupt("表 offset/length 越界", tag_s,
                              "offset=%d length=%d file=%d" %
                              (offset, length, len(raw)))
        if tag_s in tables:
            raise FontCorrupt("表标签重复", tag_s)
        data = bytes(raw[offset:offset + length])
        tables[tag_s] = TableRecord(tag_s, checksum, offset, length, data)
    return SFNT(bytes(raw), scaler.decode("latin-1"), tables)


def _printable_tag(tag):
    if len(tag) != 4:
        return False
    for ch in tag:
        if ch == " ":
            continue
        o = ord(ch)
        if o < 0x20 or o > 0x7E:
            return False
    return True


def table_checksum(data):
    padded = data + b"\x00" * ((4 - len(data) % 4) % 4)
    total = 0
    for i in range(0, len(padded), 4):
        total = (total + struct.unpack_from(">I", padded, i)[0]) & 0xFFFFFFFF
    return total
