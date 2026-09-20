"""测试/演示用小型许可字体夹具（代码构造，MIT 许可元数据）。"""
from __future__ import annotations

from . import builder as B
from . import gsubbuild as G


def _box(w=400, h=600, advance=600):
    return B.encode_simple_glyph(
        [([3], [True] * 4, [50, 50 + w, 50 + w, 50], [0, 0, h, h])],
        (50, 0, 50 + w, h), advance=advance, lsb=50)


def _dot():
    return B.encode_simple_glyph(
        [([3], [True] * 4, [200, 300, 300, 200],
          [550, 550, 650, 650])],
        (200, 550, 300, 650), advance=500, lsb=50)


def _notdef():
    return B.encode_simple_glyph(
        [([7], [True] * 8,
          [50, 550, 550, 50, 100, 500, 500, 100],
          [0, 0, 700, 700, 100, 100, 600, 600])],
        (50, 0, 550, 700), advance=600, lsb=50)


def make_demo_font(with_cycle=False):
    """夹具布局见 README：
      gid0 .notdef, gid1 A, gid2 B, gid3 f, gid4 i,
      gid5 fi ligature, gid6 A.fancy, gid7 astral(U+20492),
      gid8 combining acute(U+0301), gid9 composite Á(U+00C1)
    """
    glyphs = [
        _notdef(),
        _box(500, 700),
        _box(480, 700),
        _box(300, 700),
        _box(200, 700),
        _box(560, 700),
        _box(520, 720),
        _box(600, 700),
        _dot(),
        B.encode_composite_glyph(
            [(1, 0, 0, None), (8, 300, 0, None)],
            (50, 0, 550, 700)),
    ]
    if with_cycle:
        glyphs.append(B.encode_composite_glyph(
            [(11, 0, 0, None)], (0, 0, 600, 700)))
        glyphs.append(B.encode_composite_glyph(
            [(10, 0, 0, None)], (0, 0, 600, 700)))

    num = len(glyphs)
    glyf, loca = B.build_glyf_loca(glyphs)
    metrics = [(600, 50)] * num

    mapping = {
        0x0041: 1,
        0x0042: 2,
        0x0066: 3,
        0x0069: 4,
        0x0061: 2,
        0x0301: 8,
        0x00C1: 9,
        0x20492: 7,
    }
    uvs = {0xFE00: [(0x0041, 6)]}

    lig_sub = G.ligature_subst({3: [([4], 5)]})
    single_sub = G.single_subst_format2({1: 6})
    gsub = G.assemble_gsub(
        [("liga", [0]), ("ss01", [1])],
        [G.lookup_blob(4, lig_sub), G.lookup_blob(1, single_sub)])

    name = B.build_name([
        (0, "Copyright 2026 GlyphScope Fixture. MIT License."),
        (1, "GlyphScope Demo"),
        (4, "GlyphScope Demo Regular"),
        (5, "Version 1.000"),
        (6, "GlyphScopeDemo-Regular"),
        (13, "This fixture font is released under the MIT License. "
             "Permission is hereby granted, free of charge, to use, copy, "
             "modify, merge, publish, distribute and sublicense it."),
        (14, "https://opensource.org/licenses/MIT"),
    ])

    tables = {
        "cmap": B.build_cmap(mapping, uvs),
        "glyf": glyf,
        "head": B.build_head(),
        "hhea": B.build_hhea(num),
        "hmtx": B.build_hmtx(metrics),
        "loca": loca,
        "maxp": B.build_maxp(num),
        "name": name,
        "post": B.build_post_format3(),
        "GSUB": G.assemble_gsub(
            [("liga", [0]), ("ss01", [1])],
            [G.lookup_blob(4, lig_sub), G.lookup_blob(1, single_sub)]),
    }
    return B.pack_sfnt(tables)
