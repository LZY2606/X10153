"""把版本化计划导出为实际子集文件。

约束：
  - 保留输入字体的许可证元数据（name 全 ID / 全语言，含 nameID 13/14、
    copyright nameID 0、trademark 7）；
  - 保留计划声明的布局特性，subsetter 因此自动补入的 glyph 会在报告中
    显式列出（``subsetter_added``），避免计划与实物悄悄不一致；
  - 时间戳固定（``created/modified`` 归零），同一计划重复导出字节一致；
  - 支持“只生成计划”：不调用本模块即可。
"""

import io
from typing import Tuple

from fontTools.subset import Options, Subsetter

from .errors import GlyphScopeError

# nameID：* 表示保留全部（fontTools 支持通配）。
LICENSE_NAME_IDS = ["*"]
LICENSE_LANGS = ["*"]
EPOCH = 0x0C388800  # 2000-01-01，fontTools 默认低位时间戳；确定性即可。


def export_subset(raw_bytes: bytes, plan: dict) -> Tuple[bytes, dict]:
    """根据计划生成子集字节与导出报告。"""
    from .loader import analyze_bytes

    analysis = analyze_bytes(raw_bytes)
    font = analysis.font
    order = analysis.glyph_order
    planned_gids = sorted(g["gid"] for g in plan["glyphs"])
    planned_names = [order[gid] for gid in planned_gids if gid < len(order)]

    options = Options()
    options.name_IDs = LICENSE_NAME_IDS
    options.name_languages = LICENSE_LANGS
    options.name_legacy = True
    options.recommended_glyphs = False
    options.notdef_outline = True
    options.glyph_names = True
    options.legacy_cmap = False
    options.symbol_cmap = False
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.drop_tables += ["DSIG"]
    # 仅保留计划声明的特性；空列表表示关闭所有布局特性保留。
    options.layout_features = list(plan["parameters"]["features"])

    subsetter = Subsetter(options=options)
    subsetter.populate(glyphs=planned_names)
    try:
        subsetter.subset(font)
    except Exception as exc:  # 导出失败不应破坏原始分析对象语义
        raise GlyphScopeError("子集导出失败：%s" % exc)

    final_order = list(font.getGlyphOrder())
    actual_names = set(final_order)
    planned_set = set(planned_names)
    added = sorted(actual_names - planned_set)
    missing_from_export = sorted(planned_set - actual_names)

    suffix = ".otf" if ("CFF " in font or "CFF2" in font) else ".ttf"
    buf = io.BytesIO()
    _set_deterministic_head(font)
    font.save(buf)
    data = buf.getvalue()

    license_check = _license_check(analysis, font)

    report = {
        "plan_id": plan["plan_id"],
        "spec_version": plan["spec_version"],
        "suffix": suffix,
        "planned_glyph_count": len(planned_names),
        "exported_glyph_count": len(final_order),
        "planned_gids": planned_gids,
        "exported_order": final_order,
        "subsetter_added": added,
        "planned_but_absent": missing_from_export,
        "features_kept": list(plan["parameters"]["features"]),
        "license": license_check,
        "deterministic_timestamp": True,
    }
    return data, report


def _set_deterministic_head(font):
    head = font.get("head")
    if head is not None:
        head.created = EPOCH
        head.modified = EPOCH


def _license_check(before_analysis, after_font) -> dict:
    """核对许可证 name 记录是否完整保留。"""
    before = {}
    license_kinds = {"copyright", "trademark", "license", "license_url"}
    for key, value in before_analysis.licenses.items():
        kind = key.split(".")[0]
        if kind not in license_kinds:
            continue
        before.setdefault(kind, {})[key] = value
    after = {}
    name = after_font.get("name")
    if name is not None:
        for record in name.names:
            if record.nameID in (0, 7, 13, 14):
                try:
                    value = record.toUnicode()
                except Exception:
                    value = record.string.decode("latin-1", "replace")
                kind = {0: "copyright", 7: "trademark", 13: "license", 14: "license_url"}[
                    record.nameID
                ]
                after.setdefault(kind, []).append(value)
    preserved = {}
    for kind, values in before.items():
        after_values = after.get(kind, [])
        preserved[kind] = all(v in after_values for v in values.values())
    return {"before": before, "after": after, "all_preserved": all(preserved.values()) if preserved else True}
