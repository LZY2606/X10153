"""字体加载与严格校验。

流程：
1. 嗅探 sfnt 头（TTF/OTF），拒绝 woff/woff2/TTC（可解包后另行上传）；
2. 手动遍历表目录，检查每张表的 offset/length 是否越界、目录是否重叠破坏；
3. 用 fontTools 打开并强制解析关键表（cmap、glyf/loca、GSUB/GPOS），
   任何异常统一转成 :class:`FontCorruptError`；
4. 对 glyf 复合 glyph 做传递闭包并检测循环；
5. 生成表摘要与许可证元数据。

出错即“整份字体进入隔离”，不会返回半棵依赖树。
原始字节始终由 storage 层先落盘，再调用本模块。
"""

import hashlib
import io
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from fontTools.ttLib import TTFont

from .errors import FontCorruptError

SFNT_TAGS = {0x00010000: "TrueType", 0x74727565: "TrueType", 0x4F54544F: "CFF"}
LICENSE_NAME_IDS = {
    0: "copyright",
    13: "license",
    14: "license_url",
    2: "family",
    4: "full_name",
    6: "postscript_name",
    7: "trademark",
}
REQUIRED_TABLES = ("cmap", "head", "hhea", "hmtx", "maxp", "name", "post")


@dataclass
class CompositeReport:
    """glyf 复合依赖分析结果（GID 邻接表）。"""

    edges: Dict[int, List[int]] = field(default_factory=dict)
    order: List[int] = field(default_factory=list)
    cycles: List[List[int]] = field(default_factory=list)


@dataclass
class FontAnalysis:
    """成功解析后的完整只读视图。"""

    font: TTFont
    flavor: str
    sfnt_version: int
    num_glyphs: int
    glyph_order: List[str]
    name_to_gid: Dict[str, int]
    table_directory: List[dict]
    table_summary: List[dict]
    licenses: Dict[str, str]
    composite: CompositeReport
    has_glyf: bool
    has_cff: bool
    has_gsub: bool
    has_gpos: bool


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_header(data: bytes) -> Tuple[int, str]:
    if len(data) < 12:
        raise FontCorruptError("文件不足 12 字节，缺少 sfnt 头", "sfnt")
    (version,) = struct.unpack(">I", data[:4])
    if version in (0x774F4646, 0x774F4632):
        raise FontCorruptError("woff/woff2 压缩格式暂不支持，请解包后上传 TTF/OTF", "sfnt")
    if version == 0x74746366:
        raise FontCorruptError("TTC 字体集合暂不支持，请先拆分为单个字体", "sfnt")
    if version not in SFNT_TAGS:
        raise FontCorruptError("无法识别的 sfnt version 0x%08X" % version, "sfnt")
    return version, SFNT_TAGS[version]


def parse_directory(data: bytes) -> Tuple[int, List[dict]]:
    (num_tables,) = struct.unpack(">H", data[4:6])
    entries = []
    pos = 12
    if pos + num_tables * 16 > len(data):
        raise FontCorruptError(
            "表目录声明 %d 张表但文件长度不足" % num_tables, "sfnt"
        )
    for _ in range(num_tables):
        tag = data[pos : pos + 4].decode("latin-1")
        checksum, offset, length = struct.unpack(">III", data[pos + 4 : pos + 16])
        pos += 16
        if length == 0 and tag != "DSIG":
            raise FontCorruptError("表 %s 长度为 0" % tag, tag)
        if offset + length > len(data):
            raise FontCorruptError(
                "表 %s 越界：offset=%d length=%d 文件长度=%d"
                % (tag, offset, length, len(data)),
                tag,
            )
        entries.append(
            {"tag": tag, "checksum": checksum, "offset": offset, "length": length}
        )
    # 目录重叠往往意味着手工破坏；DSIG 允许零长，已在上面排除其余零长。
    spans = sorted((e["offset"], e["offset"] + e["length"], e["tag"]) for e in entries)
    for idx in range(1, len(spans)):
        if spans[idx][0] < spans[idx - 1][1]:
            raise FontCorruptError(
                "表 %s 与 %s 的字节区间重叠" % (spans[idx - 1][2], spans[idx][2]),
                "sfnt",
            )
    tags = {e["tag"] for e in entries}
    missing = [t for t in REQUIRED_TABLES if t not in tags]
    if missing:
        raise FontCorruptError("缺少必要表：%s" % ", ".join(missing), "sfnt")
    return num_tables, entries


def analyze_bytes(data: bytes) -> FontAnalysis:
    """解析并严格校验字体字节，成功返回 :class:`FontAnalysis`。

    任何失败都抛 :class:`FontCorruptError`，调用方应把字体隔离。
    """
    sfnt_version, flavor = sniff_header(data)
    _, directory = parse_directory(data)

    try:
        font = TTFont(io.BytesIO(data), recalcBBoxes=False, lazy=False)
    except FontCorruptError:
        raise
    except Exception as exc:  # fontTools 内部异常种类很多，统一收口
        raise FontCorruptError("fontTools 打开失败：%s" % exc, "sfnt")

    try:
        order = list(font.getGlyphOrder())
    except Exception as exc:
        raise FontCorruptError("无法读取 glyph order：%s" % exc, "maxp")

    name_to_gid = {name: gid for gid, name in enumerate(order)}

    # loca 边界（TrueType 轮廓）。
    has_glyf = "glyf" in font
    has_cff = "CFF " in font or "CFF2" in font
    if has_glyf:
        _verify_loca(font, len(data), directory)
    composite = CompositeReport()
    if has_glyf:
        composite = _analyze_glyf(font, name_to_gid)

    # 强制反编译并重新编译布局 / cmap 表，逼出内部越界 offset。
    _force_table(font, "cmap")
    if "GSUB" in font:
        _force_table(font, "GSUB")
    if "GPOS" in font:
        _force_table(font, "GPOS")

    table_summary = _summarize_tables(font, directory)
    licenses = _extract_licenses(font)

    return FontAnalysis(
        font=font,
        flavor=flavor,
        sfnt_version=sfnt_version,
        num_glyphs=len(order),
        glyph_order=order,
        name_to_gid=name_to_gid,
        table_directory=directory,
        table_summary=table_summary,
        licenses=licenses,
        composite=composite,
        has_glyf=has_glyf,
        has_cff=has_cff,
        has_gsub="GSUB" in font,
        has_gpos="GPOS" in font,
    )


def _verify_loca(font, file_len, directory):
    try:
        glyf = font["glyf"]
        loca = font["loca"]
        maxp = font["maxp"]
        # fontTools 的 loca 反编译后统一为长格式（绝对、半字单位已还原）。
        offsets = list(loca)
        glyf_entry = next((e for e in directory if e["tag"] == "glyf"), None)
        if glyf_entry is None:
            raise FontCorruptError("有 loca 但缺少 glyf 表", "glyf")
        for off in offsets:
            if off < 0 or off > glyf_entry["length"]:
                raise FontCorruptError(
                    "loca offset %d 超出 glyf 表长度 %d"
                    % (off, glyf_entry["length"]),
                    "loca",
                )
        if len(offsets) != maxp.numGlyphs + 1:
            raise FontCorruptError(
                "loca 条目数(%d)与 maxp.numGlyphs+1(%d)不一致"
                % (len(offsets), maxp.numGlyphs + 1),
                "loca",
            )
        # 强制展开所有 glyph，触发 glyf 内部解析。
        for name in font.getGlyphOrder():
            glyf[name].expand(glyf)
    except FontCorruptError:
        raise
    except Exception as exc:
        raise FontCorruptError("glyf/loca 校验失败：%s" % exc, "glyf")


def _analyze_glyf(font, name_to_gid) -> CompositeReport:
    report = CompositeReport()
    glyf = font["glyf"]
    order = font.getGlyphOrder()
    for name in order:
        glyph = glyf[name]
        gid = name_to_gid[name]
        try:
            glyph.expand(glyf)
        except Exception as exc:
            raise FontCorruptError("glyph %s 展开失败：%s" % (name, exc), "glyf")
        if glyph.isComposite():
            deps = []
            for comp in glyph.components:
                cname = getattr(comp, "glyphName", None)
                if cname not in name_to_gid:
                    raise FontCorruptError(
                        "复合 glyph %s 引用未知组件 %s" % (name, cname), "glyf"
                    )
                deps.append(name_to_gid[cname])
            report.edges[gid] = deps

    # 迭代 DFS 求传递闭包并检测环（防环：gray/black 着色）。
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {gid: WHITE for gid in range(len(order))}
    closure: Dict[int, set] = {}

    def visit(start):
        stack = [(start, list(report.edges.get(start, [])))]
        color[start] = GRAY
        closure[start] = set()
        path = [start]
        while stack:
            node, pending = stack[-1]
            advanced = False
            while pending:
                child = pending.pop(0)
                if color[child] == GRAY:
                    cyc_start = path.index(child)
                    report.cycles.append(path[cyc_start:] + [child])
                    continue
                closure[node].add(child)
                if color[child] == WHITE:
                    color[child] = GRAY
                    path.append(child)
                    closure.setdefault(child, set())
                    stack.append((child, list(report.edges.get(child, []))))
                    advanced = True
                    break
                if color[child] == BLACK:
                    closure[node].update(closure.get(child, set()))
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()
                if stack:
                    parent = stack[-1][0]
                    closure[parent].update(closure[node])
                    closure[parent].add(node)

    for gid in range(len(order)):
        if color[gid] == WHITE:
            visit(gid)

    report.order = list(range(len(order)))
    report.transitive = {
        gid: sorted(closure.get(gid, set())) for gid in range(len(order))
    }
    if report.cycles:
        names = order
        desc = "; ".join(
            " -> ".join(names[g] for g in cyc) for cyc in report.cycles[:5]
        )
        raise FontCorruptError("复合 glyph 依赖存在环：%s" % desc, "glyf")
    return report


def _force_table(font, tag):
    try:
        table = font[tag]
        # 触发所有子结构反编译。
        if tag == "GSUB" or tag == "GPOS":
            _walk_layout(table)
        elif tag == "cmap":
            for sub in table.tables:
                _ = sub.cmap
        # 重新编译可暴露 offset 不一致等损坏。
        table.compile(font)
    except FontCorruptError:
        raise
    except Exception as exc:
        raise FontCorruptError("%s 表解析/编译失败：%s" % (tag, exc), tag)


def _walk_layout(table):
    inner = getattr(table, "table", None)
    if inner is None:
        return
    if getattr(inner, "LookupList", None) is not None:
        for lookup in inner.LookupList.Lookup:
            for st in lookup.SubTable:
                _touch_subtable(st)
    if getattr(inner, "FeatureList", None) is not None:
        for fr in inner.FeatureList.FeatureRecord:
            _ = list(fr.Feature.LookupListIndex)
    if getattr(inner, "ScriptList", None) is not None:
        for sr in inner.ScriptList.ScriptRecord:
            _ = sr.Script.DefaultLangSys
            for lr in sr.Script.LangSysRecord:
                _ = lr.LangSys.FeatureIndex


def _touch_subtable(st):
    """安全地触碰子表内部结构，逼出延迟解析问题。"""
    ext = getattr(st, "extSubTable", None)
    if ext is not None:
        _touch_subtable(ext)
        return
    for attr in (
        "mapping",
        "Coverage",
        "GlyphCount",
        "SubstCount",
        "SubRuleSet",
        "SubClassSet",
        "ChainSubRuleSet",
        "ChainSubClassSet",
        "BacktrackCoverage",
        "LookAheadCoverage",
        "InputCoverage",
        "ClassDef",
        "BacktrackClassDef",
        "InputClassDef",
        "LookAheadClassDef",
        "SubstLookupRecord",
    ):
        try:
            getattr(st, attr)
        except AttributeError:
            continue
    for attr in ("SubRuleSet", "ChainSubRuleSet"):
        rset_list = getattr(st, attr, None)
        if rset_list:
            for rset in rset_list:
                if rset is None:
                    continue
                for r in getattr(rset, "SubRule", None) or getattr(
                    rset, "ChainSubRule", None
                ) or []:
                    for rec in r.SubstLookupRecord:
                        _ = rec.SequenceIndex
                        _ = rec.LookupListIndex
    for attr in ("SubClassSet", "ChainSubClassSet"):
        cset_list = getattr(st, attr, None)
        if cset_list:
            for cset in cset_list:
                if cset is None:
                    continue
                for r in getattr(cset, "SubClassRule", None) or getattr(
                    cset, "ChainSubRule", None
                ) or []:
                    _ = list(r.Class)
    ligatures = getattr(st, "ligatures", None)
    if isinstance(ligatures, dict):
        for ligs in ligatures.values():
            for lig in ligs:
                _ = lig.CompCount
                _ = lig.Component
                _ = lig.LigGlyph
    for rec in getattr(st, "SubstLookupRecord", None) or []:
        _ = rec.SequenceIndex
        _ = rec.LookupListIndex


def _summarize_tables(font, directory) -> List[dict]:
    rows = []
    present = set(font.keys())
    for entry in directory:
        tag = entry["tag"]
        row = {
            "tag": tag,
            "offset": entry["offset"],
            "length": entry["length"],
            "present": tag in present,
        }
        rows.append(row)
    return rows


def _extract_licenses(font) -> Dict[str, str]:
    out: Dict[str, str] = {}
    name = font.get("name")
    if name is None:
        return out
    for record in name.names:
        if record.nameID in LICENSE_NAME_IDS:
            key = "%s.%d.%d" % (
                LICENSE_NAME_IDS[record.nameID],
                record.platformID,
                record.platEncID,
            )
            try:
                value = record.toUnicode()
            except Exception:
                value = record.string.decode("latin-1", "replace")
            if value and (key not in out or LICENSE_NAME_IDS[record.nameID] == "license"):
                out[key] = value
    return out
