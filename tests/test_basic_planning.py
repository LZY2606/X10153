from glyphscope.fixtures import make_demo_font
from glyphscope.font import LoadedFont
from glyphscope.planner import (PlanOptions, SampleRun, make_plan,
                                 POLICY_NONE, POLICY_CLOSURE)
from glyphscope.subset import export_subset


def load():
    return LoadedFont(make_demo_font())


def gids(plan):
    return [g["glyph_index"] for g in plan["glyphs"]]


def test_basic_cmap_and_astral_plane():
    font = load()
    plan = make_plan(
        font, [SampleRun("AB\U00020492", "s")],
        PlanOptions(features=[], feature_policy=POLICY_NONE))
    assert set(gids(plan)) == {0, 1, 2, 7}
    origins = {g["glyph_index"]: [o["kind"] for o in g["origins"]]
               for g in plan["glyphs"]}
    assert "cmap" in origins[7]
    assert plan["stats"]["num_missing_chars"] == 0


def test_variation_selectors_non_default_and_base():
    font = load()
    plan = make_plan(
        font, [SampleRun("A\ufe00", "s")],
        PlanOptions(features=[], feature_policy=POLICY_NONE))
    # 非默认 VS glyph gid6，基字 glyph gid1 也作为依赖保留
    assert set(gids(plan)) == {0, 1, 6}
    g6 = next(g for g in plan["glyphs"] if g["glyph_index"] == 6)
    assert any(o["kind"] == "variation_non_default"
               and o["variation_selector"] == 0xFE00
               for o in g6["origins"])


def test_combining_and_composite_transitive():
    font = load()
    plan = make_plan(
        font, [SampleRun("\u00c1", "s")],
        PlanOptions(features=[], feature_policy=POLICY_NONE))
    # gid9 是复合 glyph，依赖 gid1 和 gid8（传递闭合）
    assert set(gids(plan)) == {0, 1, 8, 9}
    g8 = next(g for g in plan["glyphs"] if g["glyph_index"] == 8)
    assert any(o["kind"] == "composite_dependency"
               and o["parent_glyph"] == 9 for o in g8["origins"])
    comp_edges = [e for e in plan["edges"]
                  if e["kind"] == "composite_component"]
    assert (9, 1) in {(e["source_gid"], e["target_gid"]) for e in comp_edges}
    assert (9, 8) in {(e["source_gid"], e["target_gid"]) for e in comp_edges}


def test_combining_mark_sequence_cluster():
    font = load()
    plan = make_plan(
        font, [SampleRun("A\u0301", "s")],  # A + combining acute
        PlanOptions(features=[], feature_policy=POLICY_NONE))
    tokens = plan["preview_tokens"]
    assert len(tokens) == 1
    assert tokens[0]["codepoints"] == [0x41, 0x301]
    assert any(o["kind"] == "combining_sequence"
               for o in tokens[0]["reasons"])


def test_missing_mapping_distinct_from_notdef():
    font = load()
    # Z 没有 cmap 映射（missing），构造一个映射到 .notdef 的字符用于区分：
    # 夹具中没有“映射到 gid0”的字符；使用格式上未覆盖字符 Z 验证 missing。
    plan = make_plan(
        font, [SampleRun("Z", "s")],
        PlanOptions(features=[], feature_policy=POLICY_NONE))
    assert plan["missing"] and plan["missing"][0]["codepoints"] == [ord("Z")]
    assert plan["stats"]["num_missing_chars"] == 1
    assert plan["stats"]["num_notdef_chars"] == 0
    # 缺失字符不产生任何业务 glyph（gid0 是子集约定保留的 .notdef）
    business = [g for g in gids(plan) if g != 0]
    assert business == []
