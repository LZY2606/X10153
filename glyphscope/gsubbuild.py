"""夹具/导出共用的 GSUB 二进制构造（仅支持已解析的 4 类替换）。"""
from __future__ import annotations

from .binary import Writer


def coverage_format1(glyphs):
    w = Writer()
    w.u16(1)
    w.u16(len(glyphs))
    for g in glyphs:
        w.u16(g)
    return w.bytes()


def single_subst_format2(mapping):
    gids_in = sorted(mapping)
    cov = coverage_format1(gids_in)
    w = Writer()
    w.u16(2)  # SingleSubstFormat2
    w.u16(2 + 2 + 2 + 2 * len(gids_in))  # coverage offset
    w.u16(len(gids_in))
    for g in gids_in:
        w.u16(mapping[g])
    w.raw(cov)
    return w.bytes()


def ligature_subst(ligatures):
    """ligatures: {first_gid: [([other_gids], lig_gid), ...]}"""
    ligset_blobs = {}
    for first, entries in ligatures.items():
        lig_blobs = []
        for comps, lig_gid in entries:
            lb = Writer()
            lb.u16(lig_gid)
            lb.u16(len(comps) + 1)
            for g in comps:
                lb.u16(g)
            lig_blobs.append(lb.bytes())
        ls = Writer()
        ls.u16(len(lig_blobs))
        cur = 2 + 2 * len(lig_blobs)
        for blob in lig_blobs:
            ls.u16(cur)
            cur += len(blob)
        for blob in lig_blobs:
            ls.raw(blob)
        ligset_blobs[first] = ls.bytes()

    first_gids = sorted(ligset_blobs)
    cov = coverage_format1(first_gids)
    w = Writer()
    w.u16(1)
    cov_offset = 6 + 2 * len(first_gids)
    w.u16(cov_offset)
    w.u16(len(first_gids))
    cur = cov_offset + len(cov)
    for first in first_gids:
        w.u16(cur)
        cur += len(ligset_blobs[first])
    w.raw(cov)
    for first in first_gids:
        w.raw(ligset_blobs[first])
    return w.bytes()


def multiple_subst(mapping):
    gids_in = sorted(mapping)
    cov = coverage_format1(gids_in)
    seq_blobs = []
    for g in gids_in:
        sw = Writer()
        seq = mapping[g]
        sw.u16(len(seq))
        for out in seq:
            sw.u16(out)
        seq_blobs.append(sw.bytes())
    w = Writer()
    w.u16(1)
    seq_offset_start = 6 + 2 * len(gids_in)
    cur = seq_offset_start + len(cov)
    w.u16(seq_offset_start)
    w.u16(len(gids_in))
    seq_offsets = []
    for blob in seq_blobs:
        seq_offsets.append(cur)
        cur += len(blob)
    for off in seq_offsets:
        w.u16(off)
    w.raw(cov)
    for blob in seq_blobs:
        w.raw(blob)
    return w.bytes()


def alternate_subst(mapping):
    # 结构与 multiple 相同
    return multiple_subst(mapping)


def lookup_blob(lookup_type, subtable_blob, flags=0):
    w = Writer()
    w.u16(lookup_type)
    w.u16(flags)
    w.u16(1)
    w.u16(8)
    w.raw(subtable_blob)
    return w.bytes()


def assemble_gsub(feature_specs, lookup_blobs):
    """feature_specs: list of (tag, [lookup_index,...])。

    构造 DFLT/dflt script，一个 LangSys 启用全部 features。
    lookup_blobs: list of bytes（索引即 lookup index）。
    """
    # LangSys
    langsys = Writer()
    langsys.u16(0)
    langsys.u16(0xFFFF)
    langsys.u16(len(feature_specs))
    for i in range(len(feature_specs)):
        langsys.u16(i)
    script = Writer()
    script.u16(4)
    script.u16(0)
    script.raw(langsys.bytes())
    script_blob = script.bytes()

    sl = Writer()
    sl.u16(1)
    sl.tag("DFLT")
    sl.u16(8)
    sl.raw(script_blob)
    script_list = sl.bytes()

    feature_blobs = []
    for tag, lookup_indices in feature_specs:
        fw = Writer()
        fw.u16(0)
        fw.u16(len(lookup_indices))
        for li in lookup_indices:
            fw.u16(li)
        feature_blobs.append((tag, fw.bytes()))

    fl = Writer()
    fl.u16(len(feature_blobs))
    header_len = 2 + 6 * len(feature_blobs)
    cur = header_len
    offsets = []
    for tag, blob in feature_blobs:
        offsets.append(cur)
        cur += len(blob)
    for (tag, blob), off in zip(feature_blobs, offsets):
        fl.tag(tag)
        fl.u16(off)
    for tag, blob in feature_blobs:
        fl.raw(blob)
    feature_list = fl.bytes()

    ll = Writer()
    ll.u16(len(lookup_blobs))
    base = 2 + 2 * len(lookup_blobs)
    cur = base
    offsets = []
    for blob in lookup_blobs:
        offsets.append(cur)
        cur += len(blob)
    for off in offsets:
        ll.u16(off)
    for blob in lookup_blobs:
        ll.raw(blob)
    lookup_list = ll.bytes()

    out = Writer()
    out.u16(1)
    out.u16(0)
    out.u16(10)
    fl_off = 10 + len(script_list)
    ll_off = fl_off + len(feature_list)
    out.u16(fl_off)
    out.u16(ll_off)
    out.raw(script_list)
    out.raw(feature_list)
    out.raw(lookup_list)
    return out.bytes()
