"""小型许可字体夹具构造器。

生成的 TTF 完全由本仓库代码合成，许可证写入 name 表
（nameID 0/13/14），可离线、确定性地重复构造。
覆盖：
  - 代理平面字符 U+1F600（触发 cmap format 12）；
  - variation selector U+E0101 的非默认 UVS（format 14）；
  - 组合字符 U+0301 与预组合 aacute 复合 glyph；
  - 多层复合链 A -> B -> C；
  - GSUB liga 连字 f+i -> fi；
  - GSUB calt 上下文替代 a -> aVS2（演示脚本上下文走不同 glyph）。
"""

import struct
import tempfile

from fontTools.feaLib.builder import addOpenTypeFeatures
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

LICENSE_COPYRIGHT = "Copyright 2026 GlyphScope Fixture (SIL-like permissive test license)"
LICENSE_TEXT = "GlyphScope Test Font License: permission to use/copy/subset for testing."
LICENSE_URL = "http://glyphscope.invalid/license"

GLYPH_ORDER = [
    ".notdef",
    "uni1F600",
    "a",
    "f",
    "i",
    "fi",
    "aVS",
    "aVS2",
    "uni0301",
    "acute",
    "aacute",
    "deepA",
    "deepB",
    "deepC",
]


def box_glyph(glyphs=None):
    pen = TTGlyphPen(glyphs)
    pen.moveTo((0, 0))
    pen.lineTo((400, 0))
    pen.lineTo((400, 700))
    pen.lineTo((0, 700))
    pen.closePath()
    return pen.glyph()


def composite_glyph(glyphs, components):
    pen = TTGlyphPen(glyphs)
    for idx, name in enumerate(components):
        pen.addComponent(name, (1, 0, 0, 1, idx * 300, 0))
    return pen.glyph()


def build_fixture_ttf():
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(GLYPH_ORDER)
    fb.setupCharacterMap(
        {
            0x1F600: "uni1F600",
            ord("a"): "a",
            ord("f"): "f",
            ord("i"): "i",
            0x0301: "uni0301",
            0xE000: "deepA",
        }
    )
    glyphs = {name: box_glyph(None) for name in GLYPH_ORDER}
    glyphs["aacute"] = composite_glyph(glyphs, ["a", "acute"])
    glyphs["deepA"] = composite_glyph(glyphs, ["deepB"])
    glyphs["deepB"] = composite_glyph(glyphs, ["deepC"])
    glyphs["deepC"] = composite_glyph(glyphs, ["acute"])
    metrics = {name: (700, 50) for name in GLYPH_ORDER}
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": "GlyphScopeFixture",
            "styleName": "Regular",
            "fullName": "GlyphScopeFixture Regular",
            "copyright": LICENSE_COPYRIGHT,
            "licenseDescription": LICENSE_TEXT,
            "licenseInfoURL": LICENSE_URL,
        }
    )
    fb.setupOS2()
    fb.setupPost()
    fb.setupHead(unitsPerEm=1000)
    fb.font["glyf"] = table = newTable("glyf")
    table.glyphs = glyphs
    table.glyphOrder = GLYPH_ORDER
    fb.font["loca"] = newTable("loca")
    fb.setupMaxp()

    sub = CmapSubtable.newSubtable(14)
    sub.platformID = 0
    sub.platEncID = 0
    sub.format = 14
    sub.reserved = 0
    sub.data = b""
    sub.cmap = {}
    sub.uvsDict = {0xE0101: [(ord("a"), "aVS")]}
    fb.font["cmap"].tables.append(sub)

    fea = (
        "lookup singleCtx { sub a by aVS2; } singleCtx;\n"
        "feature ccmp { sub f by f uni0301; } ccmp;\n"
        "feature liga { sub f i by fi; } liga;\n"
        "feature calt { sub a' lookup singleCtx f'; } calt;\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".fea", delete=False) as fh:
        fh.write(fea)
        fea_path = fh.name
    addOpenTypeFeatures(fb.font, fea_path)
    return fb.font


def fixture_bytes():
    import io

    font = build_fixture_ttf()
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


# ---------- 损坏夹具 ----------

def corrupt_directory_offset(data: bytes) -> bytes:
    """把第一张表的 offset 改成越过文件尾。"""
    num_tables = struct.unpack(">H", data[4:6])[0]
    assert num_tables >= 1
    buf = bytearray(data)
    # 目录条目从偏移 12 开始，offset 字段在条目内 +8。
    packed = struct.pack(">I", len(data) + 500)
    buf[20:24] = packed
    return bytes(buf)


def truncate_table(data: bytes, tag: str) -> bytes:
    """把指定表的 length 改大，造成越界读取。"""
    num_tables = struct.unpack(">H", data[4:6])[0]
    buf = bytearray(data)
    for idx in range(num_tables):
        pos = 12 + idx * 16
        if bytes(buf[pos : pos + 4]).decode("latin-1") == tag:
            buf[pos + 12 : pos + 16] = struct.pack(">I", len(data) * 4)
            return bytes(buf)
    raise AssertionError("表不存在：%s" % tag)


def patch_glyf_component_cycle(data: bytes) -> bytes:
    """构造复合 glyph 组件环：把 deepC 的唯一组件改指向 deepA。

    deepA(GID11) -> deepB(12) -> deepC(13)，改后 deepC -> deepA 成环。
    """
    font = TTFont(io_BytesIO(data), recalcBBoxes=False)
    glyf_table = font.reader.tables["glyf"]
    loca_table = font.reader.tables["loca"]
    raw = data[glyf_table.offset : glyf_table.offset + glyf_table.length]
    offsets = list(font["loca"])
    start, end = offsets[13], offsets[14]
    assert end > start, "deepC 应为复合 glyph"
    # ARG_1_AND_2_ARE_WORDS(1<<0) | MORE_COMPONENTS(1<<5) | WE_HAVE_A_SCALE 等不要求；
    # flags = 0x0001（word args），glyphIndex 位于 flags 后 +6。
    glyph_blob = bytearray(raw[start:end])
    assert struct.unpack(">h", glyph_blob[0:2])[0] == -1, "缺少复合标志 -1"
    # glyf 复合头 10 字节（contours -1 + bbox），组件记录从偏移 10 开始：
    # flags(2) + glyphIndex(2) + args。
    flags = struct.unpack(">H", glyph_blob[10:12])[0]
    glyph_index_pos = 12
    struct.pack_into(">H", glyph_blob, glyph_index_pos, 11)
    new_raw = bytes(raw[:start]) + bytes(glyph_blob) + bytes(raw[end:])
    buf = bytearray(data)
    buf[glyf_table.offset : glyf_table.offset + glyf_table.length] = new_raw
    return bytes(buf)


def io_BytesIO(data):
    import io

    return io.BytesIO(data)
