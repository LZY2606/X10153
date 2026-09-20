"""glyf/loca/hmtx/head/maxp/hhea 解析。

复合 glyph 的组件关系在这里一次性全量解析，循环组件直接抛
:class:`FontCorrupt`，由上层隔离整份字体。
"""
from __future__ import annotations

from .binary import Reader
from .errors import FontCorrupt
from .models import CompositePart, GlyphInfo

ARG_1_AND_2_ARE_WORDS = 0x0001
ARGS_ARE_XY_VALUES = 0x0002
ROUND_XY_TO_GRID = 0x0004
WE_HAVE_A_SCALE = 0x0008
# 0x0010 保留
MORE_COMPONENTS = 0x0020
WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
WE_HAVE_A_TWO_BY_TWO = 0x0080
WE_HAVE_INSTRUCTIONS = 0x0100
USE_MY_METRICS = 0x0200
OVERLAP_COMPOUND = 0x0400
SCALED_COMPONENT_OFFSET = 0x0800
UNSCALED_COMPONENT_OFFSET = 0x1000


def parse_head(r):
    r.require(54)
    version = r.u32()
    if version not in (0x00010000, 0x00020000):
        raise FontCorrupt("非法 head.version", "head", hex(version))
    r.skip(4)  # fontRevision
    r.skip(4)  # checksumAdjustment
    magic = r.u32()
    if magic != 0x5F0F3CF5:
        raise FontCorrupt("head.magicNumber 错误", "head")
    flags = r.u16()
    units_per_em = r.u16()
    if not 16 <= units_per_em <= 16384:
        raise FontCorrupt("head.unitsPerEm 非法", "head", str(units_per_em))
    r.skip(8)  # created
    r.skip(8)  # modified
    x_min, y_min, x_max, y_max = r.i16(), r.i16(), r.i16(), r.i16()
    mac_style = r.u16()
    lowest_rec_ppem = r.u16()
    font_direction_hint = r.i16()
    index_to_loc_format = r.i16()
    glyph_data_format = r.i16()
    if index_to_loc_format not in (0, 1):
        raise FontCorrupt("head.indexToLocFormat 非法", "head",
                          str(index_to_loc_format))
    if glyph_data_format != 0:
        raise FontCorrupt("head.glyphDataFormat 非 0", "head")
    return {
        "flags": flags,
        "units_per_em": units_per_em,
        "x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max,
        "mac_style": mac_style,
        "lowest_rec_ppem": lowest_rec_ppem,
        "font_direction_hint": font_direction_hint,
        "index_to_loc_format": index_to_loc_format,
    }


def parse_maxp(r):
    version = r.u32()
    if version == 0x00005000:
        return {"version": version, "num_glyphs": r.u16()}
    if version == 0x00010000:
        r.require(28)
        num_glyphs = r.u16()
        rest = r.bytes(24)
        return {"version": version, "num_glyphs": num_glyphs, "tt_rest": rest}
    raise FontCorrupt("非法 maxp.version", "maxp", hex(version))


def parse_hhea(r):
    r.require(36)
    version = r.u32()
    if version not in (0x00010000, 0x00020000):
        raise FontCorrupt("非法 hhea.version", "hhea")
    r.skip(30)
    num_long = r.u16()
    return {"version": version, "number_of_h_metrics": num_long}


def parse_hmtx(r, num_glyphs, num_long):
    if num_long > num_glyphs:
        raise FontCorrupt("hhea.numberOfHMetrics 大于 glyph 数", "hmtx")
    metrics = []
    for i in range(num_long):
        metrics.append((r.u16(), r.i16()))
    if num_long == 0:
        raise FontCorrupt("numberOfHMetrics 为 0", "hmtx")
    last_advance = metrics[-1][0]
    bearings = []
    for i in range(num_glyphs - num_long):
        bearings.append(r.i16())
    result = [None] * num_glyphs
    for i, (adv, lsb) in enumerate(metrics):
        result[i] = (adv, lsb)
    for i, lsb in enumerate(bearings):
        result[num_long + i] = (last_advance, lsb)
    if r.remaining() not in (0,):
        # vhea/vmtx 场景下容忍多余字节？TrueType hmtx 不应有多余
        raise FontCorrupt("hmtx 长度与 glyph 数不符", "hmtx")
    return result


def parse_loca(r, num_glyphs, fmt):
    if fmt == 0:
        needed = 2 * (num_glyphs + 1)
        r.require(needed)
        offsets = []
        for i in range(num_glyphs + 1):
            offsets.append(r.u16() * 2)
    else:
        needed = 4 * (num_glyphs + 1)
        r.require(needed)
        offsets = [r.u32() for _ in range(num_glyphs + 1)]
    for i in range(len(offsets) - 1):
        if offsets[i] > offsets[i + 1]:
            raise FontCorrupt("loca offset 非单调", "loca")
    return offsets


def parse_glyf(data, loca_offsets, num_glyphs):
    """返回 glyph_info 列表；解析全部 glyph 的头部与复合结构。

    简单字形的轮廓坐标也一并解析（供本地 SVG 预览）。
    """
    glyf_len = len(data)
    infos = []
    for gid in range(num_glyphs):
        off = loca_offsets[gid]
        end = loca_offsets[gid + 1]
        if off == end:
            infos.append(_empty_glyph(gid))
            continue
        if end > glyf_len:
            raise FontCorrupt("loca 指向 glyf 之外", "glyf",
                              "gid=%d end=%d glyf=%d" % (gid, end, glyf_len))
        r = Reader(data, off, end, table="glyf")
        infos.append(_parse_one(gid, r, glyf_len, loca_offsets, num_glyphs))
    _detect_component_cycles(infos)
    return infos


def _empty_glyph(gid):
    return GlyphInfo(index=gid, name="", number_of_contours=0,
                     has_outline=False)


def _parse_one(gid, r, glyf_len, loca_offsets, num_glyphs):
    contours = r.i16()
    x_min, y_min, x_max, y_max = r.i16(), r.i16(), r.i16(), r.i16()
    info = GlyphInfo(index=gid, name="", number_of_contours=contours,
                     x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
    if contours == 0:
        # 空字形（有 bbox 但无数据的情况极少，按空处理）
        info.has_outline = False
        if r.remaining():
            raise FontCorrupt("空 glyph 含多余数据", "glyf", "gid=%d" % gid)
        return info
    if contours > 0:
        _parse_simple(r, contours)
        return info
    # 复合字形
    info.is_composite = True
    info.has_outline = False
    while True:
        flags = r.u16()
        comp_gid = r.u16()
        if comp_gid >= num_glyphs:
            raise FontCorrupt("复合 glyph 组件 gid 越界", "glyf",
                              "gid=%d comp=%d num=%d" %
                              (gid, comp_gid, num_glyphs))
        if flags & ARG_1_AND_2_ARE_WORDS:
            a1 = r.i16()
        else:
            a1 = r.i8()
        if flags & ARG_1_AND_2_ARE_WORDS:
            a2 = r.i16()
        else:
            a2 = r.i8()
        if flags & WE_HAVE_A_SCALE:
            s = r.i16() / 16384.0
            matrix = (s, 0.0, 0.0, s, 0.0, 0.0)
        elif flags & WE_HAVE_AN_X_AND_Y_SCALE:
            sx = r.i16() / 16384.0
            sy = r.i16() / 16384.0
            matrix = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif flags & WE_HAVE_A_TWO_BY_TWO:
            xx = r.i16() / 16384.0
            xy = r.i16() / 16384.0
            yx = r.i16() / 16384.0
            yy = r.i16() / 16384.0
            matrix = (xx, yx, xy, yy, 0.0, 0.0)
        else:
            matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        info.components.append(
            CompositePart(comp_gid, flags, a1, a2, matrix))
        if not (flags & MORE_COMPONENTS):
            if flags & WE_HAVE_INSTRUCTIONS:
                instr_len = r.u16()
                r.skip(instr_len)
            break
    if r.remaining():
        raise FontCorrupt("复合 glyph 尾部多余数据", "glyf", "gid=%d" % gid)
    return info


def _parse_simple(r, contours):
    end_pts = [r.u16() for _ in range(contours)]
    instruction_len = r.u16()
    r.skip(instruction_len)
    num_points = end_pts[-1] + 1
    if num_points == 0:
        raise FontCorrupt("简单 glyph 声明 0 个点", "glyf")
    if len(set(end_pts)) != len(end_pts) or end_pts != sorted(end_pts):
        raise FontCorrupt("endPtsOfContours 非法", "glyf")
    flags = []
    while len(flags) < num_points:
        flag = r.u8()
        repeat = 1
        if flag & 0x08:
            repeat = r.u8()
            if repeat == 0 or len(flags) + repeat > num_points:
                raise FontCorrupt("简单 glyph 标志 repeat 非法", "glyf")
        flags.extend([flag] * repeat)

    xs = [0] * num_points
    ys = [0] * num_points
    x = 0
    for i, flag in enumerate(flags):
        if flag & 0x02:
            delta = r.u8()
            if not (flag & 0x10):
                delta = -delta
        elif flag & 0x10:
            delta = 0
        else:
            delta = r.i16()
        x += delta
        xs[i] = x
    y = 0
    for i, flag in enumerate(flags):
        if flag & 0x04:
            delta = r.u8()
            if not (flag & 0x20):
                delta = -delta
        elif flag & 0x20:
            delta = 0
        else:
            delta = r.i16()
        y += delta
        ys[i] = y
    return end_pts, flags, xs, ys


def parse_glyf_outline(data, off, end):
    """供预览：解析单个简单 glyph，返回 (end_pts, on_curve, xs, ys)。"""
    r = Reader(data, off, end, table="glyf")
    contours = r.i16()
    r.skip(8)
    if contours <= 0:
        return None
    end_pts, flags, xs, ys = _parse_simple(r, contours)
    on_curve = [bool(f & 0x01) for f in flags]
    return end_pts, on_curve, xs, ys


def _detect_component_cycles(infos):
    color = [0] * len(infos)  # 0 white, 1 gray, 2 black

    def visit(gid, stack):
        if color[gid] == 1:
            cycle = " -> ".join("gid%d" % g for g in stack + [gid])
            raise FontCorrupt("复合 glyph 组件依赖环", "glyf", cycle)
        if color[gid] == 2:
            return
        color[gid] = 1
        info = infos[gid]
        if info.is_composite:
            for part in info.components:
                visit(part.glyph_index, stack + [gid])
        color[gid] = 2

    for gid in range(len(infos)):
        if color[gid] == 0:
            visit(gid, [])


def transitive_components(infos, seed_gids):
    """给定种子 glyph，返回复合依赖传递闭包（含种子本身），已防环。"""
    result = set()
    stack = list(seed_gids)
    while stack:
        gid = stack.pop()
        if gid in result:
            continue
        if not (0 <= gid < len(infos)):
            continue
        result.add(gid)
        info = infos[gid]
        if info.is_composite:
            for part in info.components:
                if part.glyph_index not in result:
                    stack.append(part.glyph_index)
    return result


def flatten_components(infos, gid, _depth=0):
    """返回复合 glyph 用到的简单（非复合）gid 集合，含仿射变换。"""
    if _depth > len(infos):
        # 正常字体解析时已检环；这里只做深度保护
        raise FontCorrupt("复合 glyph 组件依赖环", "glyf", "gid=%d" % gid)
    info = infos[gid]
    if not info.is_composite:
        return [(gid, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))]
    out = []
    for part in info.components:
        for child, m2 in flatten_components(infos, part.glyph_index,
                                            _depth + 1):
            out.append((child, _multiply(part.transform, m2)))
    return out


def _multiply(a, b):
    a_xx, a_yx, a_xy, a_yy, a_dx, a_dy = a
    b_xx, b_yx, b_xy, b_yy, b_dx, b_dy = b
    return (
        a_xx * b_xx + a_xy * b_yx,
        a_yx * b_xx + a_yy * b_yx,
        a_xx * b_xy + a_xy * b_yy,
        a_yx * b_xy + a_yy * b_yy,
        a_xx * b_dx + a_xy * b_dy + a_dx,
        a_yx * b_dx + a_yy * b_dy + a_dy,
    )
