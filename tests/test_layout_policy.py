from glyphscope.font import LoadedFont
from glyphscope.fixtures import make_demo_font
from glyphscope.planner import (PlanOptions, SampleRun, make_plan,
                                 POLICY_NONE, POLICY_BASE, POLICY_CLOSURE)
from glyphscope.subset import export_subset


def load():
    return LoadedFont(make_demo_font())


def gids(plan):
    return {g["glyph_index"] for g in plan["glyphs"]}


def test_ligature_cross_cluster():
    font = load()
    plan = make_plan(
        font, [SampleRun("fi", "s")],
        PlanOptions(features=["liga"]))
    # 连字结果 gid5 被纳入；f/gid3、i/gid4 作为触发与组件保留
    assert 5 in gids(plan)
    tokens = plan["preview_tokens"]
    assert tokens[0]["glyphs"] == [5]
    assert tokens[1]["status"] == "absorbed"
    g5 = next(g for g in plan["glyphs"] if g["glyph_index"] == 5)
    assert any(o["kind"] == "layout_ligature"
               and o["feature"] == "liga" for o in g5["origins"])


def test_feature_closure_includes_untriggered_glyphs():
    font = load()
    # 仅输入 A：ss01 single lookup 的目标 gid6 会在 closure 策略下纳入
    plan = make_plan(
        font, [SampleRun("A", "s")],
        PlanOptions(features=["ss01"], feature_policy=POLICY_CLOSURE))
    assert 6 in gids(plan)
    g6 = next(g for g in plan["glyphs"] if g["glyph_index"] == 6)
    # A 同时也会被 single 替换成 6（因为 shaping 应用 lookup）
    assert g6["included_by"]


def test_feature_base_does_not_closure_extra_targets():
    font = load()
    # base 策略：不额外纳入特性覆盖中未直接触发的 glyph
    plan = make_plan(
        font, [SampleRun("B", "s")],
        PlanOptions(features=["ss01"], feature_policy=POLICY_BASE))
    # B(gid2) 不在 ss01 coverage（只有 A/gid1），故 gid6 不纳入
    assert 6 not in gids(plan)


def test_feature_none_drops_ligature():
    font = load()
    plan = make_plan(
        font, [SampleRun("fi", "s")],
        PlanOptions(features=[], feature_policy=POLICY_NONE))
    assert 5 not in gids(plan)
    assert plan["stats"]["num_layout_substitutions"] == 0


def test_deterministic_glyph_order_and_reproducible():
    font = load()
    runs = [SampleRun("fiA\ufe00\U00020492", "s")]
    opts = PlanOptions(features=["liga", "ss01"])
    p1 = make_plan(font, runs, opts)
    p2 = make_plan(font, runs, opts)
    order1 = [g["glyph_index"] for g in p1["glyphs"]]
    assert order1 == sorted(order1)
    assert p1["content_hash"] == p2["content_hash"]
    raw1, _, _ = export_subset(font, p1)
    raw2, _, _ = export_subset(font, p2)
    assert raw1 == raw2


def test_closure_keeps_feature_functional_without_text_trigger():
    font = load()
    # 输入 B（与 liga 无关），closure 仍纳入 f/i/fi 使连字特性可用
    plan = make_plan(
        font, [SampleRun("B", "s")],
        PlanOptions(features=["liga"], feature_policy=POLICY_CLOSURE))
    assert {3, 4, 5} <= gids(plan)
    closure_origins = [g for g in plan["glyphs"]
                       if "feature_closure" in g["included_by"]]
    assert closure_origins

    from glyphscope.subset import export_subset
    from glyphscope.font import LoadedFont
    raw, gmap, _ = export_subset(font, plan)
    subset = LoadedFont(raw)
    # 特性规则与全部相关 glyph（f/i/fi）都在子集内并完成 gid 重映射
    assert subset.gsub is not None
    lig_lookup = subset.gsub["lookups"][0]
    ligsets = lig_lookup.subtables[0].rule.ligsets
    (new_components, new_lig) = list(ligsets.values())[0][0]
    assert gmap[3] in list(ligsets.keys())
    assert new_components == [gmap[4]]
    assert new_lig == gmap[5]
    # 这些 glyph 不来自样本字符，故不新增 cmap 映射（字符按样本裁剪）
    assert 0x66 not in subset.cmap and 0x69 not in subset.cmap
