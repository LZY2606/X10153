from glyphscope.loader import analyze_bytes
from glyphscope.mapper import CmapIndex
from glyphscope.segments import segment_text


def _cmap(data):
    a = analyze_bytes(data)
    return a, CmapIndex(a.font, a.name_to_gid)


def test_astral_plane_character_maps(font_bytes):
    a, cmap = _cmap(font_bytes)
    lu = cmap.lookup(0x1F600)
    assert lu["status"] == "mapped"
    assert a.glyph_order[lu["gid"]] == "uni1F600"
    assert any("fmt12" in s for s in lu["sources"])


def test_variation_selector_routes_to_different_glyph(font_bytes):
    a, cmap = _cmap(font_bytes)
    plain = cmap.lookup(ord("a"))
    variant = cmap.lookup(ord("a"), 0xE0101)
    uncovered = cmap.lookup(ord("a"), 0xE0100)
    assert a.glyph_order[plain["gid"]] == "a"
    assert variant["variant_registered"] is True
    assert a.glyph_order[variant["gid"]] == "aVS"
    assert variant["gid"] != plain["gid"]
    assert uncovered["variant_uncovered"] is True
    assert uncovered["gid"] == plain["gid"]


def test_missing_distinct_from_notdef(font_bytes):
    _a, cmap = _cmap(font_bytes)
    lu = cmap.lookup(0x4E2D)
    assert lu["status"] == "missing" and lu["gid"] is None


def test_combining_mark_segment_and_map(font_bytes):
    clusters = segment_text("a\u0301")
    assert len(clusters) == 1
    c = clusters[0]
    assert c.base == ord("a") and c.marks == [0x0301]
    a, cmap = _cmap(font_bytes)
    mark_lu = cmap.lookup(0x0301)
    assert mark_lu["status"] == "mapped"
    assert a.glyph_order[mark_lu["gid"]] == "uni0301"


def test_astral_in_segment_text():
    clusters = segment_text("\U0001F600")
    assert len(clusters) == 1
    assert clusters[0].base == 0x1F600
