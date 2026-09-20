"""GSUB 布局表解析（subset 分析所需）。

支持的替换类型：
  1 SingleSubst (format 1/2)
  2 MultipleSubst (format 1)
  3 AlternateSubst (format 1)
  4 LigatureSubst (format 1)
其它类型（context/chaining/extension/reverse 等）解析为 unsupported 节点，
导出时会被丢弃并在计划中记录 dropped_warnings。
"""
from __future__ import annotations

from .binary import Reader
from .errors import FontCorrupt

TYPE_NAMES = {
    1: "single",
    2: "multiple",
    3: "alternate",
    4: "ligature",
    5: "context",
    6: "chaining",
    7: "extension",
    8: "reverse",
}

SINGLE = 1
MULTIPLE = 2
ALTERNATE = 3
LIGATURE = 4


class Coverage:
    __slots__ = ("glyphs",)

    def __init__(self, glyphs):
        self.glyphs = glyphs


class SingleRule:
    kind = "single"

    def __init__(self, mapping):
        self.mapping = mapping  # gid_in -> gid_out


class MultipleRule:
    kind = "multiple"

    def __init__(self, mapping):
        self.mapping = mapping  # gid_in -> [gid_out,...]


class AlternateRule:
    kind = "alternate"

    def __init__(self, mapping):
        self.mapping = mapping  # gid_in -> [gid_alt,...]


class LigatureRule:
    kind = "ligature"

    def __init__(self, ligsets):
        # gid_first -> list of (components[1:], lig_gid)
        self.ligsets = ligsets


class Subtable:
    def __init__(self, rule, coverage_glyphs, raw_format):
        self.rule = rule
        self.coverage = coverage_glyphs
        self.raw_format = raw_format


class ParsedLookup:
    def __init__(self, index, lookup_type, flags, subtables, supported=True,
                 dropped_reason=None):
        self.index = index
        self.lookup_type = lookup_type
        self.flags = flags
        self.subtables = subtables
        self.supported = supported
        self.dropped_reason = dropped_reason


def _table_header(r):
    major = r.u16()
    minor = r.u16()
    if major != 1 or minor not in (0, 1):
        raise FontCorrupt("GSUB 版本非法", "GSUB", "%d.%d" % (major, minor))
    script_list_off = r.u16()
    feature_list_off = r.u16()
    lookup_list_off = r.u16()
    return major, minor, script_list_off, feature_list_off, lookup_list_off


def parse_gsub(r):
    major, minor, sl_off, fl_off, ll_off = _table_header(r)
    scripts = {}
    if sl_off:
        scripts = _parse_script_list(r, sl_off)
    features = []
    if fl_off:
        features = _parse_feature_list(r, fl_off, scripts)
    lookups = []
    if ll_off:
        lookups = _parse_lookup_list(r, ll_off)
    return {"features": features, "lookups": lookups, "version": (major, minor)}


def parse_feature_tags_only(r):
    """GPOS 只读取 feature tag 列表（摘要用）。"""
    try:
        major, minor, sl_off, fl_off, ll_off = _table_header(r)
        if not fl_off:
            return []
        sub = r.sub(fl_off)
        count = sub.u16()
        tags = []
        for i in range(count):
            tags.append(sub.tag())
            sub.skip(2)
        return tags
    except FontCorrupt:
        raise


def _parse_script_list(r, offset):
    sub = r.sub(offset)
    count = sub.u16()
    script_offs = []
    for i in range(count):
        tag = sub.tag()
        off = sub.u16()
        script_offs.append((tag, off))
    scripts = {}
    for tag, off in script_offs:
        ssub = sub.sub(off)
        default_off = ssub.u16()
        lang_count = ssub.u16()
        lang_offs = []
        for j in range(lang_count):
            lang_tag = ssub.tag()
            lang_off = ssub.u16()
            lang_offs.append((lang_tag, lang_off))
        feature_indexes = set()
        if default_off:
            feature_indexes.update(_parse_lang_sys(ssub, default_off))
        for lang_tag, lang_off in lang_offs:
            feature_indexes.update(_parse_lang_sys(ssub, lang_off))
        scripts[tag] = sorted(feature_indexes)
    return scripts


def _parse_lang_sys(r, offset):
    sub = r.sub(offset)
    sub.skip(2)  # lookupOrder
    sub.skip(2)  # requiredFeatureIndex
    count = sub.u16()
    return {sub.u16() for _ in range(count)}


def _parse_feature_list(r, offset, scripts):
    sub = r.sub(offset)
    count = sub.u16()
    rows = []
    for i in range(count):
        tag = sub.tag()
        off = sub.u16()
        rows.append((tag, off))
    features = []
    for feature_index, (tag, off) in enumerate(rows):
        fsub = sub.sub(off)
        fsub.skip(2)  # featureParams
        lookup_count = fsub.u16()
        lookup_indices = [fsub.u16() for _ in range(lookup_count)]
        script_tags = sorted(
            stag for stag, fidxs in scripts.items()
            if feature_index in fidxs)
        features.append({
            "tag": tag,
            "lookup_indices": lookup_indices,
            "scripts": script_tags,
            "default_on": _default_on(tag),
        })
    return features


def _default_on(tag):
    # 常见默认启用特性
    return tag in ("calt", "clig", "ccmp", "liga", "rlig", "locl")


def _parse_lookup_list(r, offset):
    sub = r.sub(offset)
    count = sub.u16()
    offsets = [sub.u16() for _ in range(count)]
    lookups = []
    for index, off in enumerate(offsets):
        lookups.append(_parse_lookup(sub, index, off))
    return lookups


def _parse_lookup(base, index, offset):
    r = base.sub(offset)
    lookup_type = r.u16()
    flags = r.u16()
    subtable_count = r.u16()
    subtable_offsets = [r.u16() for _ in range(subtable_count)]
    if lookup_type not in (SINGLE, MULTIPLE, ALTERNATE, LIGATURE):
        return ParsedLookup(index, lookup_type, flags, [],
                            supported=False,
                            dropped_reason="lookup 类型 %d 暂不支持"
                            % lookup_type)
    subtables = []
    for soff in subtable_offsets:
        subtables.append(_parse_subtable(r, lookup_type, soff))
    return ParsedLookup(index, lookup_type, flags, subtables, supported=True)


def _parse_coverage(base, offset):
    r = base.sub(offset)
    fmt = r.u16()
    if fmt == 1:
        count = r.u16()
        glyphs = [r.u16() for _ in range(count)]
    elif fmt == 2:
        count = r.u16()
        glyphs = []
        for i in range(count):
            start = r.u16()
            end = r.u16()
            index = r.u16()
            if start > end:
                raise FontCorrupt("Coverage range start>end", "GSUB")
            glyphs.extend(range(start, end + 1))
    else:
        raise FontCorrupt("Coverage format 非法", "GSUB", str(fmt))
    if len(glyphs) != len(set(glyphs)):
        raise FontCorrupt("Coverage glyph 重复", "GSUB")
    return glyphs


def _parse_subtable(base, lookup_type, offset):
    r = base.sub(offset)
    fmt = r.u16()

    if lookup_type == SINGLE:
        coverage_off = r.u16()
        coverage = _parse_coverage(r, coverage_off)
        mapping = {}
        if fmt == 1:
            delta = r.i16()
            for gid in coverage:
                out = gid + delta
                if not (0 <= out <= 0xFFFF):
                    raise FontCorrupt("SingleSubst delta 越界", "GSUB")
                mapping[gid] = out
        elif fmt == 2:
            glyph_count = r.u16()
            if glyph_count != len(coverage):
                raise FontCorrupt("SingleSubst glyphCount 与 coverage 不符",
                                  "GSUB")
            for gid in coverage:
                mapping[gid] = r.u16()
        else:
            raise FontCorrupt("SingleSubst format 非法", "GSUB", str(fmt))
        return Subtable(SingleRule(mapping), coverage, fmt)

    if lookup_type == MULTIPLE:
        if fmt != 1:
            raise FontCorrupt("MultipleSubst format 非 1", "GSUB", str(fmt))
        coverage_off = r.u16()
        coverage = _parse_coverage(r, coverage_off)
        count = r.u16()
        seq_offsets = [r.u16() for _ in range(count)]
        if count != len(coverage):
            raise FontCorrupt("MultipleSubst count 与 coverage 不符", "GSUB")
        mapping = {}
        for gid, soff in zip(coverage, seq_offsets):
            ssub = r.sub(soff)
            glyph_count = ssub.u16()
            if glyph_count == 0:
                raise FontCorrupt("MultipleSubst 空序列", "GSUB")
            mapping[gid] = [ssub.u16() for _ in range(glyph_count)]
        return Subtable(MultipleRule(mapping), coverage, fmt)

    if lookup_type == ALTERNATE:
        if fmt != 1:
            raise FontCorrupt("AlternateSubst format 非 1", "GSUB", str(fmt))
        coverage_off = r.u16()
        coverage = _parse_coverage(r, coverage_off)
        count = r.u16()
        set_offsets = [r.u16() for _ in range(count)]
        if count != len(coverage):
            raise FontCorrupt("AlternateSubst count 与 coverage 不符", "GSUB")
        mapping = {}
        for gid, soff in zip(coverage, set_offsets):
            ssub = r.sub(soff)
            glyph_count = ssub.u16()
            mapping[gid] = [ssub.u16() for _ in range(glyph_count)]
        return Subtable(AlternateRule(mapping), coverage, fmt)

    if lookup_type == LIGATURE:
        if fmt != 1:
            raise FontCorrupt("LigatureSubst format 非 1", "GSUB", str(fmt))
        coverage_off = r.u16()
        coverage = _parse_coverage(r, coverage_off)
        count = r.u16()
        set_offsets = [r.u16() for _ in range(count)]
        if count != len(coverage):
            raise FontCorrupt("LigatureSubst count 与 coverage 不符", "GSUB")
        ligsets = {}
        for gid, soff in zip(coverage, set_offsets):
            ssub = r.sub(soff)
            lig_count = ssub.u16()
            lig_offsets = [ssub.u16() for _ in range(lig_count)]
            entries = []
            for loff in lig_offsets:
                lsub = ssub.sub(loff)
                lig_gid = lsub.u16()
                comp_count = lsub.u16()
                if comp_count < 2:
                    raise FontCorrupt("Ligature compCount<2", "GSUB")
                components = [lsub.u16() for _ in range(comp_count - 1)]
                entries.append((components, lig_gid))
            ligsets[gid] = entries
        return Subtable(LigatureRule(ligsets), coverage, fmt)

    raise FontCorrupt("不支持的 subtable 类型", "GSUB", str(lookup_type))
