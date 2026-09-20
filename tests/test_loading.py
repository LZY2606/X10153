import pytest

from builder import (
    corrupt_directory_offset,
    patch_glyf_component_cycle,
    truncate_table,
)
from glyphscope.errors import FontCorruptError
from glyphscope.loader import analyze_bytes


def test_valid_fixture_loads(font_bytes):
    a = analyze_bytes(font_bytes)
    assert a.num_glyphs == 14
    assert a.has_gsub and a.has_glyf
    assert a.composite.cycles == []


def test_composite_transitive_closure(font_bytes):
    a = analyze_bytes(font_bytes)
    names = a.glyph_order
    aacute = names.index("aacute")
    trans = a.composite.transitive[aacute]
    assert names.index("a") in trans
    assert names.index("acute") in trans
    # 多层链 deepA -> deepB -> deepC -> acute 必须传递闭合。
    deep_a = names.index("deepA")
    chain = a.composite.transitive[deep_a]
    assert {names.index(x) for x in ("deepB", "deepC", "acute")} <= set(chain)


def test_corrupt_directory_offset_quarantines(font_bytes):
    with pytest.raises(FontCorruptError) as exc:
        analyze_bytes(corrupt_directory_offset(font_bytes))
    assert exc.value.table in ("GSUB", "sfnt")


def test_truncated_table_offset_quarantines(font_bytes):
    with pytest.raises(FontCorruptError):
        analyze_bytes(truncate_table(font_bytes, "GSUB"))


def test_component_cycle_quarantines(font_bytes):
    with pytest.raises(FontCorruptError) as exc:
        analyze_bytes(patch_glyf_component_cycle(font_bytes))
    assert exc.value.table == "glyf"
    assert "环" in str(exc.value)


def test_corrupt_upload_is_quarantined_and_blocked(service, font_bytes):
    meta = service.upload_font(corrupt_directory_offset(font_bytes), "bad.ttf")
    assert meta["quarantined"] is True
    assert "quarantine_reason" in meta
    # 隔离字体原始字节保留，但不能生成计划。
    from glyphscope.errors import QuarantinedFontError

    with pytest.raises(QuarantinedFontError):
        service.create_plan(meta["sha256"], ["a"])
