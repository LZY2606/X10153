from glyphscope.planner import PlanOptions, SampleRun


def upload(service, demo_font):
    r = service.upload(demo_font, "demo.ttf")
    assert r["status"] == "ok"
    return r["digest"]


def test_plan_versions_keep_old_reproducible(service, demo_font):
    digest = upload(service, demo_font)
    r1 = service.create_plan(
        digest, [{"text": "AB", "label": "s"}],
        features=[], feature_policy="none")
    v1 = r1["version"]
    plan1 = service.get_plan(digest, v1)

    r2 = service.create_plan(
        digest, [{"text": "AfiB", "label": "s"}],
        features=["liga"])
    v2 = r2["version"]
    assert v2 > v1

    old = service.get_plan(digest, v1)
    assert old["content_hash"] == plan1["content_hash"]
    assert {g["glyph_index"] for g in old["glyphs"]} == {0, 1, 2}


def test_same_inputs_same_content_reuses_version(service, demo_font):
    digest = upload(service, demo_font)
    r1 = service.create_plan(
        digest, [{"text": "AB", "label": "s"}],
        features=[], feature_policy="none")
    r2 = service.create_plan(
        digest, [{"text": "AB", "label": "s"}],
        features=[], feature_policy="none")
    assert r1["version"] == r2["version"]
    assert r1["content_hash"] == r2["content_hash"]


def test_compare_plans(service, demo_font):
    digest = upload(service, demo_font)
    a = service.create_plan(
        digest, [{"text": "AB", "label": "s"}],
        features=[], feature_policy="none")
    b = service.create_plan(
        digest, [{"text": "fi", "label": "s"}],
        features=["liga"])
    cmp = service.compare(service.get_plan(digest, a["version"]),
                          service.get_plan(digest, b["version"]))
    only_b = {g["glyph_index"] for g in cmp["only_in_b"]}
    assert 5 in only_b
    assert 2 in {g["glyph_index"] for g in cmp["only_in_a"]}


def test_plan_only_mode_no_export(service, demo_font):
    digest = upload(service, demo_font)
    r = service.create_plan(
        digest, [{"text": "AB", "label": "s"}],
        features=[], feature_policy="none", plan_only=True)
    assert r["export"] is None
    data, path, gmap, notes = service.export_plan(digest, r["version"])
    assert data[:4] in (b"\x00\x01\x00\x00", b"true", b"typ1")


def test_graph_build(service, demo_font):
    digest = upload(service, demo_font)
    r = service.create_plan(
        digest, [{"text": "fi\u00c1", "label": "s"}],
        features=["liga"])
    graph = service.graph(r["plan"])
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "char" in kinds and "glyph" in kinds
    link_kinds = {l["kind"] for l in graph["links"]}
    assert "composite_component" in link_kinds
    assert "layout_ligature" in link_kinds


def test_svg_preview_composite(service, demo_font):
    digest = upload(service, demo_font)
    svg = service.glyph_svg(digest, 9)
    assert svg.startswith("<svg") and "<path" in svg
