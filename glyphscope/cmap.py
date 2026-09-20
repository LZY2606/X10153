"""cmap 解析（format 0/4/6/12/14）与结构校验。"""
from __future__ import annotations

import struct

from .binary import Reader
from .errors import FontCorrupt
from .models import VariationRecord


def parse_cmap(r):
    version = r.u16()
    if version not in (0, 1):
        raise FontCorrupt("非法 cmap.version", "cmap")
    num_tables = r.u16()
    if num_tables == 0:
        raise FontCorrupt("cmap 无子表", "cmap")
    subtables = []
    for i in range(num_tables):
        platform_id = r.u16()
        encoding_id = r.u16()
        offset = r.u32()
        subtables.append((platform_id, encoding_id, offset))

    parsed = []
    mapping_layers = []  # (rank, mapping)，按 rank 从低到高叠加
    variation_records = {}
    for platform_id, encoding_id, offset in subtables:
        if offset > r.limit - r.start:
            raise FontCorrupt("cmap 子表 offset 越界", "cmap")
        sub = r.sub(offset)
        fmt = sub.u16()
        if fmt == 0:
            mapping = _parse_format0(sub)
        elif fmt == 4:
            mapping = _parse_format4(sub, table_offset=offset)
        elif fmt == 6:
            mapping = _parse_format6(sub)
        elif fmt == 12:
            mapping = _parse_format12(sub)
        elif fmt == 14:
            variation_records = _parse_format14(sub)
            continue
        else:
            raise FontCorrupt("不支持的 cmap format", "cmap", str(fmt))
        parsed.append({
            "platform_id": platform_id,
            "encoding_id": encoding_id,
            "format": fmt,
            "num_mappings": len(mapping),
        })
        rank = _subtable_rank(platform_id, encoding_id, fmt)
        mapping_layers.append((rank, mapping))

    if not mapping_layers:
        raise FontCorrupt("cmap 缺少可用的 Unicode/字符映射子表", "cmap")
    merged = {}
    for rank, mapping in sorted(mapping_layers, key=lambda x: x[0]):
        merged.update(mapping)
    return {
        "mapping": merged,
        "subtables": parsed,
        "variations": variation_records,
    }


def _subtable_rank(platform_id, encoding_id, fmt):
    if platform_id == 3 and encoding_id == 10 and fmt == 12:
        return 60
    if platform_id == 3 and encoding_id == 1 and fmt == 4:
        return 50
    if platform_id == 0 and fmt == 12:
        return 45
    if platform_id == 0 and fmt == 4:
        return 40
    if platform_id == 0 and fmt == 6:
        return 30
    if platform_id == 1 and encoding_id == 0 and fmt == 0:
        return 20
    if platform_id == 1 and encoding_id == 0 and fmt == 4:
        return 15
    return 5


def _parse_format0(r):
    length = r.u16()
    if length != 262:
        raise FontCorrupt("cmap format0 长度非法", "cmap", str(length))
    r.skip(2)
    data = r.bytes(256)
    if r.remaining():
        raise FontCorrupt("cmap format0 尾部多余", "cmap")
    return {cp: gid for cp, gid in enumerate(data)}


def _parse_format4(r, table_offset=0):
    length = r.u16()
    language = r.u16()
    seg_x2 = r.u16()
    if seg_x2 == 0 or seg_x2 % 2:
        raise FontCorrupt("cmap format4 segCountX2 非法", "cmap")
    seg_count = seg_x2 // 2
    if length < 16 + 8 * seg_count:
        raise FontCorrupt("cmap format4 length 非法", "cmap")
    if length > r.limit - r.start:
        raise FontCorrupt("cmap format4 length 超出子表", "cmap")
    b = r.data
    base = r.start
    end_codes = [struct.unpack_from(">H", b, base + 14 + 2 * i)[0]
                 for i in range(seg_count)]
    reserved = struct.unpack_from(">H", b, base + 14 + 2 * seg_count)[0]
    if reserved != 0:
        raise FontCorrupt("cmap format4 reservedPad 非 0", "cmap")
    start_base = base + 16 + 2 * seg_count
    delta_base = start_base + 2 * seg_count
    offset_base = delta_base + 2 * seg_count
    glyph_array_base = offset_base + 2 * seg_count
    start_codes = [struct.unpack_from(">H", b, start_base + 2 * i)[0]
                   for i in range(seg_count)]
    id_deltas = [struct.unpack_from(">h", b, delta_base + 2 * i)[0]
                 for i in range(seg_count)]
    id_offsets = [struct.unpack_from(">H", b, offset_base + 2 * i)[0]
                  for i in range(seg_count)]
    tail_end = base + length
    if tail_end > r.limit:
        raise FontCorrupt("cmap format4 超出 cmap 表", "cmap")

    mapping = {}
    for seg in range(seg_count):
        start = start_codes[seg]
        end = end_codes[seg]
        if start > end:
            raise FontCorrupt("cmap format4 segment start>end", "cmap")
        if start == 0xFFFF and end == 0xFFFF:
            continue
        delta = id_deltas[seg]
        range_offset = id_offsets[seg]
        for cp in range(start, end + 1):
            if range_offset == 0:
                gid = (cp + delta) & 0xFFFF
            else:
                glyph_pos = (offset_base + seg * 2 + range_offset
                             + (cp - start) * 2)  # rangeOffset 已含基址
                if glyph_pos + 2 > tail_end:
                    raise FontCorrupt("cmap format4 glyphIdArray 越界",
                                      "cmap")
                gid = struct.unpack_from(">H", b, glyph_pos)[0]
                gid = (gid + delta) & 0xFFFF
            mapping[cp] = gid
    return mapping


def _read_u16_at(r, pos):
    saved = r.pos
    r.seek(r.start + pos)
    v = r.u16()
    r.seek(saved)
    return v


def _parse_format6(r):
    length = r.u16()
    r.skip(2)
    first = r.u16()
    count = r.u16()
    if length != 10 + 2 * count or first + count > 0x10000:
        raise FontCorrupt("cmap format6 length/count 非法", "cmap")
    mapping = {}
    for i in range(count):
        gid = r.u16()
        mapping[first + i] = gid
    if r.remaining():
        raise FontCorrupt("cmap format6 尾部多余", "cmap")
    return mapping


def _parse_format12(r):
    reserved = r.u16()
    if reserved != 0:
        raise FontCorrupt("cmap format12 reserved 非 0", "cmap")
    length = r.u32()
    sub_len = r.limit - r.start
    if length < 16 or length > sub_len:
        raise FontCorrupt("cmap format12 length 越界", "cmap")
    r.skip(4)  # language
    num_groups = r.u32()
    if length != 16 + 12 * num_groups:
        raise FontCorrupt("cmap format12 length 与 group 数不符", "cmap")
    mapping = {}
    prev_end = -1
    for i in range(num_groups):
        start = r.u32()
        end = r.u32()
        start_gid = r.u32()
        if start > end or start <= prev_end:
            raise FontCorrupt("cmap format12 group 非法/未排序", "cmap")
        count = end - start + 1
        if start_gid + count > 0x10000:
            raise FontCorrupt("cmap format12 glyphId 越界", "cmap")
        for cp in range(start, end + 1):
            mapping[cp] = start_gid + (cp - start)
        prev_end = end
    expected_end = r.start + length
    if r.pos > expected_end:
        raise FontCorrupt("cmap format12 超出声明长度", "cmap")
    return mapping


def _parse_format14(r):
    length = r.u32()
    if length < 10 or length > r.limit - r.start:
        raise FontCorrupt("cmap format14 length 越界", "cmap")
    num_var_selector = r.u32()
    if length < 10 + 11 * num_var_selector:
        raise FontCorrupt("cmap format14 varSelector 数越界", "cmap")
    records = {}
    selector_rows = []
    for i in range(num_var_selector):
        var_selector = r.u24()
        default_uvs_offset = r.u32()
        non_default_offset = r.u32()
        selector_rows.append((var_selector, default_uvs_offset,
                              non_default_offset))

    for var_selector, default_off, nondefault_off in selector_rows:
        if var_selector in records:
            raise FontCorrupt("cmap format14 variation selector 重复",
                              "cmap")
        default_gid = None
        non_default_gid = None
        if default_off:
            if default_off >= length:
                raise FontCorrupt("cmap format14 defaultUVS 越界", "cmap")
            sub = r.sub(default_off)
            count = sub.u32()
            if default_off + 4 + 4 * count > length:
                raise FontCorrupt("cmap format14 defaultUVS 越界", "cmap")
            # 默认 UVS：由 base cmap 决定 glyph，这里记录起始/补充平面
            ranges = []
            for j in range(count):
                start = sub.u24()
                cnt_plus = sub.u8()
                ranges.append((start, start + cnt_plus))
            default_gid = -1  # 哨兵：存在默认 UVS 覆盖，解析期记录
            records[var_selector] = VariationRecord(
                var_selector, ranges, None)
        if nondefault_off:
            if nondefault_off >= length:
                raise FontCorrupt("cmap format14 nonDefaultUVS 越界", "cmap")
            sub = r.sub(nondefault_off)
            count = sub.u32()
            if nondefault_off + 4 + 5 * count > length:
                raise FontCorrupt("cmap format14 nonDefaultUVS 越界", "cmap")
            uvs_mappings = []
            for j in range(count):
                cp = sub.u24()
                gid = sub.u16()
                uvs_mappings.append((cp, gid))
            if records.get(var_selector) is not None:
                old = records[var_selector]
                records[var_selector] = VariationRecord(
                    var_selector, old.default_glyph, uvs_mappings)
            else:
                records[var_selector] = VariationRecord(
                    var_selector, None, uvs_mappings)
    expected_end = r.start + length
    if r.pos > expected_end:
        raise FontCorrupt("cmap format14 超出声明长度", "cmap")
    return records
