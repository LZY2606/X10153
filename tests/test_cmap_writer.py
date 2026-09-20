from glyphscope import builder as B
from glyphscope.font import LoadedFont


def test_format4_glyph_array_non_contiguous(tmp_path):
    # 构造同一连续字符段但 gid 不连续，强制 rangeOffset/glyphIdArray 路径
    mapping = {0x100: 3, 0x101: 7, 0x102: 2}
    fmt4 = B.encode_cmap_format4(mapping)
    # 用最小 sfnt 装载该 cmap：直接验证 builder 公式产出可解析结构
    num_glyphs = 8
    glyf, loca = B.build_glyf_loca([b""] * num_glyphs)
    tables = {
        "cmap": B.build_cmap(mapping),
        "glyf": glyf,
        "head": B.build_head(x_max=0, y_max=0),
        "hhea": B.build_hhea(num_glyphs),
        "hmtx": B.build_hmtx([(500, 0)] * num_glyphs),
        "loca": loca,
        "maxp": B.build_maxp(num_glyphs),
        "name": B.build_name([(1, "T"), (13, "MIT test license")]),
        "post": B.build_post_format3(),
    }
    font = LoadedFont(B.pack_sfnt(tables))
    assert font.cmap[0x100] == 3
    assert font.cmap[0x101] == 7
    assert font.cmap[0x102] == 2
    assert font.meta.license_text == "MIT test license"
