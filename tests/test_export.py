from fontTools.ttLib import TTFont

from builder import LICENSE_COPYRIGHT, LICENSE_TEXT, LICENSE_URL
from glyphscope.exporter import export_subset


def _plan(service, uploaded):
    return service.create_plan(
        uploaded["sha256"],
        ["fi a \U0001F600"],
        features=["liga"],
        closure="none",
    )


def test_export_preserves_license_metadata(service, uploaded, font_bytes):
    plan = _plan(service, uploaded)
    data, report = export_subset(font_bytes, plan)
    assert report["license"]["all_preserved"] is True
    subset = TTFont(__import__("io").BytesIO(data))
    name_strings = []
    for record in subset["name"].names:
        name_strings.append(record.toUnicode())
    assert LICENSE_TEXT in name_strings
    assert LICENSE_URL in name_strings
    assert LICENSE_COPYRIGHT in name_strings


def test_export_is_deterministic(service, uploaded, font_bytes):
    plan = _plan(service, uploaded)
    data1, r1 = export_subset(font_bytes, plan)
    data2, r2 = export_subset(font_bytes, plan)
    assert data1 == data2
    assert r1["exported_order"] == r2["exported_order"]


def test_export_report_lists_actual_glyphs(service, uploaded, font_bytes):
    plan = _plan(service, uploaded)
    data, report = export_subset(font_bytes, plan)
    subset = TTFont(__import__("io").BytesIO(data))
    assert set(report["exported_order"]) == set(subset.getGlyphOrder())
    # .notdef 必然保留；fi 因 liga 保留而存在。
    assert ".notdef" in report["exported_order"]
    assert "fi" in report["exported_order"]
    # 报告必须透明呈现 subsetter 自动补入的 glyph。
    assert "subsetter_added" in report


def test_service_export_roundtrip(service, uploaded):
    plan = _plan(service, uploaded)
    path, report = service.export_plan(plan["plan_id"])
    fetched = service.export_bytes(plan["plan_id"])
    assert fetched is not None and fetched.startswith(b"\x00\x01\x00\x00")
    assert service.export_report(plan["plan_id"])["plan_id"] == plan["plan_id"]


def test_plan_only_mode_writes_no_subset(service, uploaded):
    plan = _plan(service, uploaded)
    service.store.save_plan(plan)
    assert service.store.export_path(plan["plan_id"]) is None
    assert service.store.get_export_report(plan["plan_id"]) is None
