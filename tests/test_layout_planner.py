from glyphscope.layout import GsubEngine
from glyphscope.loader import analyze_bytes


def _engine(data):
    a = analyze_bytes(data)
    return a, GsubEngine(a.font, a.glyph_order, a.name_to_gid)


def _base_nodes(cmap, cps, vss=None):
    vss = vss or {}
    nodes = []
    for cp in cps:
        lu = cmap.lookup(cp, vss.get(cp))
        assert lu["status"] == "mapped"
        nodes.append({"gid": lu["gid"], "cps": [cp], "kind": "base", "history": []})
    return nodes


def test_ligature_action_traced(font_bytes):
    a, eng = _engine(font_bytes)
    cmap = _cmap(font_bytes)[1]
    nodes = _base_nodes(cmap, [ord("f"), ord("i")])
    out, actions = eng.shape(nodes, ["liga"])
    assert len(out) == 1
    assert a.glyph_order[out[0]["gid"]] == "fi"
    assert actions[0]["detail"]["rule"] == "ligature"
    assert actions[0]["input_names"] == ["f", "i"]
    assert actions[0]["output_names"] == ["fi"]


def test_contextual_substitution_routes_by_script_context(font_bytes):
    # calt 上下文 lookup 把 a 替成 aVS2（与 VS17 的 aVS 区分）。
    a, eng = _engine(font_bytes)
    cmap = _cmap(font_bytes)[1]
    nodes = _base_nodes(cmap, [ord("a"), ord("f")])
    out, actions = eng.shape(nodes, ["calt"])
    assert a.glyph_order[out[0]["gid"]] == "aVS2"
    assert any("calt" in act["feature"] for act in actions)
    # 没有后续 f 时上下文不匹配，保持 a。
    only_a = _base_nodes(cmap, [ord("a")])
    out2, actions2 = eng.shape(only_a, ["calt"])
    assert a.glyph_order[out2[0]["gid"]] == "a" and actions2 == []


def _cmap(data):
    a = analyze_bytes(data)
    from glyphscope.mapper import CmapIndex

    return a, CmapIndex(a.font, a.name_to_gid)


def test_plan_glyph_origins_and_components(service, uploaded, font_bytes):
    sha = uploaded["sha256"]
    plan = service.create_plan(
        sha,
        ["fi af \U0001F600", "a\U000E0101", "a"],
        features=["liga", "calt"],
        closure="none",
    )
    by_name = {g["name"]: g for g in plan["glyphs"]}
    assert "fi" in by_name
    fi_origins = by_name["fi"]["origins"]
    assert any(o["kind"] == "layout" and o["rule"] == "ligature" for o in fi_origins)
    # VS 选择的 aVS 与 calt 上下文选择的 aVS2 都可被追溯。
    assert "aVS" in by_name and "aVS2" in by_name
    vs_origin = [o for g in plan["glyphs"] for o in g["origins"] if o["kind"] == "variation"]
    assert vs_origin and vs_origin[0]["variation_selector"] == "U+E0101"


def test_plan_components_transitive(service, uploaded):
    sha = uploaded["sha256"]
    plan = service.create_plan(sha, [""], features=[], closure="none")
    names = {g["name"] for g in plan["glyphs"]}
    assert {"deepA", "deepB", "deepC", "acute"} <= names
    deep_c = next(g for g in plan["glyphs"] if g["name"] == "deepC")
    assert any(o["kind"] == "component" for o in deep_c["origins"])


def test_missing_and_notdef_distinction(service, uploaded):
    sha = uploaded["sha256"]
    plan = service.create_plan(
        sha, ["\u4e2d a"], features=[], closure="none"
    )
    missing_cps = {m["cp"] for m in plan["missing"]}
    assert 0x4E2D in missing_cps
    # 正常映射的 a 绝不出现在 notdef_refs。
    assert all(m["cp"] != ord("a") for m in plan["notdef_refs"])


def test_deterministic_glyph_order_and_plan_id(service, uploaded):
    sha = uploaded["sha256"]
    p1 = service.create_plan(sha, ["fi a"], features=["liga"], closure="none")
    p2 = service.create_plan(sha, ["fi a"], features=["liga"], closure="none")
    assert p1["plan_id"] == p2["plan_id"]
    gids = [g["gid"] for g in p1["glyphs"]]
    assert gids == sorted(gids)


def test_plan_versions_survive_sample_and_policy_change(service, uploaded):
    sha = uploaded["sha256"]
    base = service.create_plan(sha, ["fi a"], features=["liga"], closure="none")
    changed_text = service.create_plan(sha, ["fi a b"], features=["liga"], closure="none")
    changed_policy = service.create_plan(sha, ["fi a"], features=["liga"], closure="full")
    changed_features = service.create_plan(sha, ["fi a"], features=["calt", "liga"], closure="none")
    ids = {base["plan_id"], changed_text["plan_id"], changed_policy["plan_id"],
           changed_features["plan_id"]}
    assert len(ids) == 4
    # 旧计划仍然存在且可取回重现。
    again = service.get_plan(base["plan_id"])
    assert again["samples"] == base["samples"]
    assert again["glyphs"] == base["glyphs"]


def test_closure_full_includes_untriggered_feature_targets(service, uploaded):
    sha = uploaded["sha256"]
    # 文本不含 fi 连字触发，但 full 策略应把 fi 纳入。
    none_plan = service.create_plan(sha, ["a"], features=["liga"], closure="none")
    full_plan = service.create_plan(sha, ["a"], features=["liga"], closure="full")
    none_names = {g["name"] for g in none_plan["glyphs"]}
    full_names = {g["name"] for g in full_plan["glyphs"]}
    assert "fi" not in none_names
    assert "fi" in full_names
    fi_glyph = next(g for g in full_plan["glyphs"] if g["name"] == "fi")
    assert any(o["kind"] == "closure" and o["policy"] == "full" for o in fi_glyph["origins"])


def test_closure_reachable_includes_static_reachable(service, uploaded):
    sha = uploaded["sha256"]
    plan = service.create_plan(sha, ["f"], features=["liga"], closure="reachable")
    names = {g["name"] for g in plan["glyphs"]}
    # 从 f 静态可达 liga 的目标 fi，即使文本里没有 i 触发。
    assert "fi" in names
    origin = [o for g in plan["glyphs"] if g["name"] == "fi" for o in g["origins"]]
    assert any(o["kind"] == "closure" and o["policy"] == "reachable" for o in origin)


def test_closure_reachable_excludes_unrelated(service, uploaded):
    sha = uploaded["sha256"]
    plan = service.create_plan(sha, ["\U0001F600"], features=["liga"], closure="reachable")
    names = {g["name"] for g in plan["glyphs"]}
    assert "fi" not in names


def test_multiple_substitution_and_layout_summary(font_bytes):
    a, eng = _engine(font_bytes)
    cmap = _cmap(font_bytes)[1]
    nodes = _base_nodes(cmap, [ord("f")])
    out, actions = eng.shape(nodes, ["ccmp"])
    assert [a.glyph_order[n["gid"]] for n in out] == ["f", "uni0301"]
    assert actions[0]["detail"]["rule"] == "multiple"
    summary = eng.summarize()
    tags = {f["tag"] for f in summary["gsub"]["features"]}
    assert {"liga", "calt", "ccmp"} <= tags
    assert summary["gpos"]["present"] is False
