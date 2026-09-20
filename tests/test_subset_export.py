from glyphscope.font import LoadedFont
from glyphscope.fixtures import make_demo_font
from glyphscope.planner import PlanOptions, SampleRun, make_plan
from glyphscope.subset import export_subset


def load():
    return LoadedFont(make_demo_font())


def plan(font, text, **kw):
    opts = PlanOptions(**kw)
    return make_plan(font, [SampleRun(text, "s")], opts)


def test_export_preserves_license_metadata():
    font = load()
    p = plan(font, "AB", features=[], feature_policy="none")
    raw, gmap, notes = export_subset(font, p)
    subset = LoadedFont(raw)
    assert subset.meta.license_text == font.meta.license_text
    assert subset.meta.license_url == font.meta.license_url
    assert subset.names[14] == font.names[14]


def test_export_roundtrip_subset_cmaps_and_composites():
    font = load()
    p = plan(font, "A\ufe00 fi \u00c1 \U00020492",
             features=["liga", "ss01"])
    raw, gmap, notes = export_subset(font, p)
    subset = LoadedFont(raw)
    # 普通字符
    assert subset.cmap[0x41] == gmap[1]
    # 非默认 VS
    vs_gid, kind = subset.variation_glyph(0x41, 0xFE00)
    assert kind == "non_default"
    assert vs_gid == gmap[6]
    # 复合 glyph 组件 gid 已重写且仍闭合
    new_gid_c1 = gmap[9]
    comps = {part.glyph_index for part in subset.glyphs[new_gid_c1].components}
    assert comps == {gmap[1], gmap[8]}
    # GSUB 连字在新 gid 空间仍成立
    p2 = make_plan(
        subset, [SampleRun("fi", "s")],
        PlanOptions(features=["liga"]))
    assert any(g["glyph_index"] == gmap[5] for g in p2["glyphs"])


def test_plan_only_and_export_bytes_deterministic():
    font = load()
    p1 = plan(font, "AB", features=[], feature_policy="none")
    p2 = plan(font, "AB", features=[], feature_policy="none")
    a, _, _ = export_subset(font, p1)
    b, _, _ = export_subset(font, p2)
    assert a == b
