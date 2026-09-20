"""小型 TrueType 字体构造器（测试夹具）与 SFNT 打包。

纯标准库实现，供测试构造确定性字体；subset 导出复用其中的写表原语。
"""
from __future__ import annotations

import struct

from .binary import Writer
from .sfnt import table_checksum


# ---- 最小轮廓编码（简单字形：矩形/三角形） ----

def encode_simple_glyph(contours, bbox, advance=600, lsb=0):
    """contours: 多轮廓点 (end_pts, on_curve, xs, ys)，全部 on-curve。"""
    w = Writer()
    num_contours = len(contours)
    w.i16(num_contours)
    w.i16(bbox[0]); w.i16(bbox[1]); w.i16(bbox[2]); w.i16(bbox[3])
    all_x, all_y, all_on, end_pts = [], [], [], []
    for end_pts_c, on_curve, xs, ys in contours:
        all_on.extend(on_curve)
        all_x.extend(xs)
        all_y.extend(ys)
        end_pts.append(end_pts_c[-1])
    for ep in end_pts:
        w.u16(ep)
    w.u16(0)  # instructionLength

    x_flags, x_bytes = _encode_coords(all_x, 0x02, 0x10)
    y_flags, y_bytes = _encode_coords(all_y, 0x04, 0x20)
    for i, on in enumerate(all_on):
        flag = 0x01 if on else 0
        flag |= x_flags[i] | y_flags[i]
        w.u8(flag)
    w.raw(x_bytes)
    w.raw(y_bytes)
    return w.bytes()


def _encode_coords(vals, short_bit, same_bit):
    """编码一条坐标序列。

    short_bit=0x02/same_bit=0x10 用于 x；0x04/0x20 用于 y。
    返回 (每点标志, 坐标字节)。
    """
    flags = []
    bw = Writer()
    prev = 0
    for v in vals:
        d = v - prev
        prev = v
        if d == 0:
            flags.append(same_bit)
        elif -255 <= d <= 255:
            flags.append(short_bit | (same_bit if d > 0 else 0))
            bw.u8(abs(d))
        else:
            flags.append(0)
            bw.i16(d)
    return flags, bw.bytes()


def encode_composite_glyph(components, bbox):
    """components: list of (gid, arg1, arg2, scale_or_none)。"""
    w = Writer()
    w.i16(-1)
    w.i16(bbox[0]); w.i16(bbox[1]); w.i16(bbox[2]); w.i16(bbox[3])
    for i, (gid, ax, ay, scale) in enumerate(components):
        flags = 0x0002 | 0x0020  # ARGS_ARE_XY_VALUES | MORE_COMPONENTS
        if abs(ax) <= 127 and abs(ay) <= 127:
            pass
        else:
            flags |= 0x0001
        if scale is not None:
            flags |= 0x0008
        if i == len(components) - 1:
            flags &= ~0x0020
        w.u16(flags)
        w.u16(gid)
        if flags & 0x0001:
            w.i16(ax); w.i16(ay)
        else:
            w.i8(_sbyte(ax)); w.i8(_sbyte(ay))
        if scale is not None:
            w.i16(round(scale * 16384))
    return w.bytes()


def _sbyte(v):
    v = int(v)
    return v if -128 <= v <= 127 else 0


def empty_glyph():
    return b""


def build_glyf_loca(glyph_buffers):
    """glyph_buffers: gid -> bytes（空字形为 b""）。返回 glyf, loca(long)。"""
    out = bytearray()
    offsets = []
    for buf in glyph_buffers:
        offsets.append(len(out))
        out.extend(buf)
        if len(out) % 2:
            out.append(0)
    offsets.append(len(out))
    lw = Writer()
    for off in offsets:
        lw.u32(off)
    return bytes(out), lw.bytes()


# ---- 基本表 ----

def build_head(units_per_em=1000, index_to_loc_format=1,
               x_min=0, y_min=0, x_max=600, y_max=700):
    w = Writer()
    w.u32(0x00010000)
    w.u32(0x00010000)      # fontRevision
    w.u32(0)               # checksumAdjustment（打包时回填）
    w.u32(0x5F0F3CF5)      # magic
    w.u16(0x000B)
    w.u16(units_per_em)
    w.u32(0); w.u32(0)     # created
    w.u32(0); w.u32(0)     # modified
    w.i16(x_min); w.i16(y_min); w.i16(x_max); w.i16(y_max)
    w.u16(0)               # macStyle
    w.u16(8)               # lowestRecPPEM
    w.i16(2)               # fontDirectionHint
    w.i16(index_to_loc_format)
    w.i16(0)               # glyphDataFormat
    return w.bytes()


def build_maxp(num_glyphs):
    w = Writer()
    w.u32(0x00010000)
    w.u16(num_glyphs)
    # numGlyphs 后 13 个 u16（maxPoints..maxSubroutineCalls）
    for value in (0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0):
        w.u16(value)
    return w.bytes()


def build_hhea(number_of_h_metrics):
    w = Writer()
    w.u32(0x00010000)      # version
    w.i16(800)             # ascender
    w.i16(-200)            # descender
    w.i16(0)               # lineGap
    w.u16(1000)            # advanceWidthMax
    w.i16(50)              # minLeftSideBearing
    w.i16(0)               # minRightSideBearing
    w.i16(1000)            # xMaxExtent
    w.i16(1)               # caretSlopeRise
    w.i16(0)               # caretSlopeRun
    w.i16(0)               # caretOffset
    w.i16(0); w.i16(0); w.i16(0); w.i16(0)  # reserved
    w.i16(0)               # metricDataFormat
    w.u16(number_of_h_metrics)
    return w.bytes()


def build_hmtx(metrics):
    w = Writer()
    for advance, lsb in metrics:
        w.u16(advance); w.i16(lsb)
    return w.bytes()


def build_post_format3():
    w = Writer()
    w.u32(0x00030000)
    w.u32(0)          # italicAngle
    w.i16(-100)       # underlinePosition
    w.i16(50)         # underlineThickness
    w.u32(0)          # isFixedPitch
    w.u32(0); w.u32(0); w.u32(0); w.u32(0)
    return w.bytes()


# ---- cmap ----

def build_cmap(mapping, non_default_uvs=None, keep_zero=False):
    """mapping: {cp: gid}; non_default_uvs: {vs: [(base_cp, gid),...]}"""
    filt = (lambda items: items) if keep_zero else (
        lambda items: [(cp, g) for cp, g in items if g != 0])
    bmp = {cp: g for cp, g in filt(mapping.items()) if cp <= 0xFFFF}
    astral = {cp: g for cp, g in filt(mapping.items()) if cp > 0xFFFF}
    fmt4 = encode_cmap_format4(bmp) if bmp else b""
    fmt12 = encode_cmap_format12(astral) if astral else b""
    fmt14 = encode_cmap_format14(non_default_uvs or {})

    w = Writer()
    w.u16(0)  # version
    records = []
    if fmt4:
        records.append((3, 1))
    if fmt12:
        records.append((3, 10))
    if fmt14:
        records.append((0, 5))
    w.u16(len(records))
    offset = 4 + 8 * len(records)
    blobs = []
    for platform_id, encoding_id in records:
        w.u16(platform_id)
        w.u16(encoding_id)
        w.u32(offset)
        if (platform_id, encoding_id) in ((3, 1), (0, 3)):
            blobs.append(fmt4)
        elif encoding_id == 10:
            blobs.append(fmt12)
        else:
            blobs.append(fmt14)
        offset += len(blobs[-1])
    for blob in blobs:
        w.raw(blob)
    return w.bytes()


def encode_cmap_format4(mapping):
    cps = sorted(mapping)
    segments = []
    i = 0
    while i < len(cps):
        start_cp = cps[i]
        j = i
        while j + 1 < len(cps) and cps[j + 1] == cps[j] + 1:
            j += 1
        end_cp = cps[j]
        gids = [mapping[cp] for cp in range(start_cp, end_cp + 1)]
        if all(gids[k] - gids[0] == k for k in range(len(gids))):
            segments.append((start_cp, end_cp,
                             (gids[0] - start_cp) & 0xFFFF, False))
        else:
            for cp in cps[i:j + 1]:
                segments.append((cp, cp, mapping[cp], True))
        i = j + 1
    segments.append((0xFFFF, 0xFFFF, 1, False))
    seg_count = len(segments)
    seg_x2 = seg_count * 2
    entry_selector = (seg_count - 1).bit_length() - 1
    search_range = 2 * (2 ** entry_selector)
    range_shift = seg_x2 - search_range

    end_codes = Writer()
    start_codes = Writer()
    deltas = Writer()
    offsets = Writer()
    glyph_arrays = Writer()
    for seg_idx, (start_cp, end_cp, val, use_range) in enumerate(segments):
        end_codes.u16(end_cp)
        start_codes.u16(start_cp)
        if not use_range:
            deltas.i16(_to_i16(val))
            offsets.u16(0)
        else:
            deltas.i16(0)
            # rangeOffset 字段起点：header14 + (endCode+reserved+startCode+delta)*2*seg
            field_pos = 14 + seg_count * 6 + seg_idx * 2
            target_pos = 14 + seg_count * 8 + glyph_arrays.length()
            offsets.u16(target_pos - field_pos)
            glyph_arrays.u16(val)

    length = 16 + 8 * seg_count + glyph_arrays.length()
    w = Writer()
    w.u16(4)
    w.u16(length)
    w.u16(0)
    w.u16(seg_x2)
    w.u16(search_range)
    w.u16(entry_selector)
    w.u16(range_shift)
    w.raw(end_codes.bytes())
    w.u16(0)  # reservedPad
    w.raw(start_codes.bytes())
    w.raw(deltas.bytes())
    w.raw(offsets.bytes())
    w.raw(glyph_arrays.bytes())
    return w.bytes()


def _to_i16(v):
    v = v & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def encode_cmap_format12(mapping):
    groups = []
    cps = sorted(mapping)
    i = 0
    while i < len(cps):
        start = cps[i]
        start_gid = mapping[start]
        j = i
        while (j + 1 < len(cps)
               and cps[j + 1] == cps[j] + 1
               and mapping[cps[j + 1]] == mapping[cps[j]] + 1):
            j += 1
        groups.append((start, cps[j], start_gid))
        i = j + 1
    w = Writer()
    w.u16(12)
    w.u16(0)
    w.u32(16 + 12 * len(groups))
    w.u32(0)
    w.u32(len(groups))
    for start, end, gid in groups:
        w.u32(start); w.u32(end); w.u32(gid)
    return w.bytes()


def encode_cmap_format14(uvs):
    selectors = sorted(uvs)
    w = Writer()
    w.u16(14)
    length_pos = None
    # length(u32), numVarSelectorRecords(u32), records(11 each), then data
    num = len(selectors)
    header_len = 10 + 11 * num
    w.u32(0)  # length placeholder
    w.u32(num)
    data = Writer()
    data_offset = header_len
    for vs in selectors:
        w.u24(vs)
        w.u32(0)  # defaultUVSOffset
        mappings = uvs[vs]
        blob = Writer()
        blob.u32(len(mappings))
        for cp, gid in mappings:
            blob.u24(cp)
            blob.u16(gid)
        w.u32(data_offset + data.length())
        data.raw(blob.bytes())
    w.raw(data.bytes())
    out = bytearray(w.bytes())
    struct_patch_u32(out, 2, len(out))
    return bytes(out)


def struct_patch_u32(buf, pos, value):
    buf[pos:pos + 4] = struct.pack(">I", value)


def build_name(records):
    """records: list of (name_id, text)，以 Windows Unicode BMP 编码。"""
    string_data = Writer()
    header = Writer()
    header.u16(0)
    header.u16(len(records))
    string_offset_pos = 4
    # string offset 先占位
    header.u16(0)
    rows = []
    for name_id, text in records:
        encoded = text.encode("utf-16-be")
        rows.append((name_id, string_data.length(), encoded))
        string_data.raw(encoded)
    string_offset = 6 + 12 * len(records)
    out = header.bytes()
    out = out[:string_offset_pos] + struct.pack(">H", string_offset) + \
        out[string_offset_pos + 2:]
    w = Writer()
    w.raw(out)
    for name_id, off, encoded in rows:
        w.u16(3)    # platform
        w.u16(1)    # encoding
        w.u16(0x0409)
        w.u16(name_id)
        w.u16(len(encoded))
        w.u16(off)
    w.raw(string_data.bytes())
    return w.bytes()


# ---- GSUB 简化构造器 ----

def build_gsub_single(feature_tag, mapping):
    """构造一个含单个 singleSubst(format2) lookup 的 GSUB，
    并在 DFLT/dflt script 下挂 feature。"""
    gids_in = sorted(mapping)
    gids_out = [mapping[g] for g in gids_in]
    cov = _coverage_format1(gids_in)
    sub = Writer()
    sub.u16(1)      # substFormat
    coverage_offset = 2 + 2 + 2 + 2 * len(gids_in)
    sub.u16(coverage_offset)
    sub.u16(len(gids_in))
    for g in gids_out:
        sub.u16(g)
    sub.raw(cov)
    sub_blob = sub.bytes()

    lookup = Writer()
    lookup.u16(1)   # single
    lookup.u16(0)   # flag
    lookup.u16(1)   # subtableCount
    lookup.u16(8)   # subtable offset
    lookup.raw(sub_blob)
    lookup_blob = lookup.bytes()

    # LangSys
    langsys = Writer()
    langsys.u16(0)   # lookupOrder
    langsys.u16(0xFFFF)
    langsys.u16(1)
    langsys.u16(0)   # feature index 0
    langsys_blob = langsys.bytes()

    # Script table: defaultLangSys offset 4, langSysCount 0
    script = Writer()
    script.u16(4)
    script.u16(0)
    script.raw(langsys_blob)
    script_blob = script.bytes()

    # Feature: params=0, lookupCount=1 -> lookup 0
    feature = Writer()
    feature.u16(0)
    feature.u16(1)
    feature.u16(0)
    feature_blob = feature.bytes()

    return _assemble_gsub(feature_tag, script_blob, feature_blob,
                          [lookup_blob])


def build_gsub_ligature(feature_tag, ligatures):
    """ligatures: {first_gid: [([other_gids], lig_gid), ...]}"""
    ligset_blobs = {}
    for first, entries in ligatures.items():
        ls = Writer()
        ls.u16(len(entries))
        lig_blobs = []
        for comps, lig_gid in entries:
            lb = Writer()
            lb.u16(lig_gid)
            lb.u16(len(comps) + 1)
            for g in comps:
                lb.u16(g)
            lig_blobs.append(lb.bytes())
        cur = 2 + 2 * len(lig_blobs)
        for blob in lig_blobs:
            ls.u16(cur)
            cur += len(blob)
        for blob in lig_blobs:
            ls.raw(blob)
        ligset_blobs[first] = ls.bytes()

    first_gids = sorted(ligset_blobs)
    cov = _coverage_format1(first_gids)

    # LigatureSubstFormat1: format(2)+coverageOffset(2)+ligSetCount(2)
    # + offsets[count] + coverage + ligSets
    sub = Writer()
    sub.u16(1)
    count = len(first_gids)
    set_offset_start = 6 + 2 * count
    cur = set_offset_start + len(cov)
    for first in first_gids:
        sub.u16(cur)
        cur += len(ligset_blobs[first])
    # 上面先写了 coverage offset 占位 + set offsets；重新正确构造：
    sub = Writer()
    sub.u16(1)
    coverage_offset = 6 + 2 * count
    sub.u16(coverage_offset)
    sub.u16(count)
    cur = coverage_offset + len(cov)
    for first in first_gids:
        sub.u16(cur)
        cur += len(ligset_blobs[first])
    sub.raw(cov)
    for first in first_gids:
        sub.raw(ligset_blobs[first])
    sub_blob = sub.bytes()

    lookup = Writer()
    lookup.u16(4)
    lookup.u16(0)
    lookup.u16(1)
    lookup.u16(8)
    lookup.raw(sub_blob)
    return _assemble_gsub(feature_tag, None, None, [lookup.bytes()],
                          ligature=True)


def _coverage_format1(glyphs):
    w = Writer()
    w.u16(1)
    w.u16(len(glyphs))
    for g in glyphs:
        w.u16(g)
    return w.bytes()


def _assemble_gsub(feature_tag, script_blob, feature_blob, lookup_blobs,
                   ligature=False):
    """统一组装 header + scriptList + featureList + lookupList。"""
    if script_blob is None:
        # ligature 分支：构造标准 script/feature
        langsys = Writer()
        langsys.u16(0); langsys.u16(0xFFFF); langsys.u16(1); langsys.u16(0)
        script = Writer()
        script.u16(4); script.u16(0)
        script.raw(langsys.bytes())
        script_blob = script.bytes()
        feature = Writer()
        feature.u16(0); feature.u16(1); feature.u16(0)
        feature_blob = feature.bytes()

    # 1) scriptList: count, ScriptRecord{tag, offset}
    sl = Writer()
    sl.u16(1)
    sl.tag("DFLT")
    sl.u16(8)  # offset to script table
    sl.raw(script_blob)
    script_list = sl.bytes()

    # 2) featureList
    fl = Writer()
    fl.u16(1)
    fl.tag(feature_tag if len(feature_tag) == 4 else feature_tag[:4].ljust(4))
    fl.u16(8)
    fl.raw(feature_blob)
    feature_list = fl.bytes()

    # 3) lookupList
    ll = Writer()
    ll.u16(len(lookup_blobs))
    offsets = 2 + 2 * len(lookup_blobs)
    for i, blob in enumerate(lookup_blobs):
        ll.u16(offsets)
        offsets += len(blob)
    for blob in lookup_blobs:
        ll.raw(blob)
    lookup_list = ll.bytes()

    out = Writer()
    out.u16(1); out.u16(0)      # version 1.0
    out.u16(10)                # scriptListOffset
    sl_off = 10
    fl_off = sl_off + len(script_list)
    ll_off = fl_off + len(feature_list)
    # 重写偏移
    out2 = Writer()
    out2.u16(1); out2.u16(0)
    out2.u16(10)
    out2.u16(fl_off)
    out2.u16(ll_off)
    out2.raw(script_list)
    out2.raw(feature_list)
    out2.raw(lookup_list)
    return out2.bytes()


def pack_sfnt(tables, scaler=b"\x00\x01\x00\x00"):
    """tables: dict tag -> bytes。确定性排序（tag 字母序），无时间戳。"""
    tags = sorted(tables)
    num = len(tags)
    entry_selector = (num - 1).bit_length() - 1 if num else 0
    search_range = 16 * (2 ** entry_selector)
    range_shift = 16 * num - search_range

    header = Writer()
    header.raw(scaler)
    header.u16(num)
    header.u16(search_range)
    header.u16(entry_selector)
    header.u16(range_shift)

    offset = 12 + 16 * num
    records = Writer()
    body = Writer()
    body_parts = []
    for tag in tags:
        data = tables[tag]
        records.tag(tag)
        records.u32(table_checksum(data))
        records.u32(offset)
        records.u32(len(data))
        body_parts.append(data)
        padded = data + b"\x00" * ((4 - len(data) % 4) % 4)
        offset += len(padded)
    for part in body_parts:
        body.raw(part)
        body.pad4()
    raw = header.bytes() + records.bytes() + body.bytes()
    # checksumAdjustment（仅 head 存在时）
    if "head" in tables:
        total = table_checksum(raw)
        adjustment = (0xB1B0AFBA - total) & 0xFFFFFFFF
        head_off = raw.index(b"head")
        # table record: tag(4) checksum(4) offset(4) length(4)
        table_offset = struct.unpack_from(">I", raw, head_off + 8)[0]
        raw = (raw[:table_offset + 8]
               + struct.pack(">I", adjustment)
               + raw[table_offset + 12:])
    return raw
