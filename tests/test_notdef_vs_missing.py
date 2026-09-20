from glyphscope import builder as B
from glyphscope.font import LoadedFont
from glyphscope.planner import PlanOptions, SampleRun, make_plan


def _make_font(mapping):
    num = 2
    glyf, loca = B.build_glyf_loca([
        B.encode_simple_glyph(
            [([7], [True] * 8,
              [50, 550, 550, 50, 100, 500, 500, 100],
              [0, 0, 700, 700, 100, 100, 600, 600])],
            (50, 0, 550, 700)),
        B.encode_simple_glyph(
            [([3], [True] * 4, [50, 550, 550, 50], [0, 0, 700, 700])],
            (50, 0, 550, 700)),
    ])
    tables = {
        "cmap": B.build_cmap(mapping, keep_zero=True),
        "glyf": glyf,
        "head": B.build_head(),
        "hhea": B.build_hhea(num),
        "hmtx": B.build_hmtx([(600, 50), (600, 50)]),
        "loca": loca,
        "maxp": B.build_maxp(num),
        "name": B.build_name([(1, "T"), (13, "MIT")]),
        "post": B.build_post_format3(),
    }
    return LoadedFont(B.pack_sfnt(tables))


def test_missing_char_has_no_mapping():
    font = _make_font({0x41: 1})
    plan = make_plan(
        font, [SampleRun("Z", "s")],
        PlanOptions(features=[], feature_policy="none"))
    assert plan["stats"]["num_missing_chars"] == 1
    assert plan["stats"]["num_notdef_chars"] == 0
    statuses = [t["status"] for t in plan["preview_tokens"]]
    assert statuses == ["missing"]


def test_mapping_to_notdef_is_distinct():
    # B 显式映射到 gid0=.notdef，Z 完全无映射
    font = _make_font({0x41: 1, 0x42: 0})
    plan = make_plan(
        font, [SampleRun("BZ", "s")],
        PlanOptions(features=[], feature_policy="none"))
    assert plan["stats"]["num_notdef_chars"] == 1
    assert plan["stats"]["num_missing_chars"] == 1
    by_char = {t["chars"]: t["status"] for t in plan["preview_tokens"]}
    assert by_char["B"] == "notdef"
    assert by_char["Z"] == "missing"
