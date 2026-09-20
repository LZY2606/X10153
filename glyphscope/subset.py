"""离线 TrueType 子集导出。

- glyph 按确定性 gid 升序映射到新 gid（0=.notdef 始终保留为 gid0）
- 重建 cmap / glyf / loca / hmtx / head / maxp / hhea / post
- name 表原样复制（许可证元数据完整保留）
- GSUB 仅重写计划启用且解析支持的 lookup，其余丢弃并记录
"""
from __future__ import annotations

import struct

from . import builder as B
from . import gsubbuild as G
from .builder import pack_sfnt


def export_subset(font, plan):
    old_gids = sorted(g["glyph_index"] for g in plan["glyphs"])
    if 0 not in old_gids:
        old_gids = [0] + old_gids
    old_to_new = {old: new for new, old in enumerate(old_gids)}
    new_num = len(old_gids)

    # glyf/loca：复制原始 glyph 字节，复合 glyph 的组件 gid 重写
    glyph_buffers = []
    for old in old_gids:
        glyph_buffers.append(_rebuild_glyf_record(font, old, old_to_new))
    glyf, loca = B.build_glyf_loca(glyph_buffers)

    # hmtx：原 metrics 搬移
    metrics = []
    for old in old_gids:
        metrics.append(font.metrics[old])
    hmtx = B.build_hmtx(metrics)

    # cmap：从计划 glyph 反推 codepoint（origin 中含 cmap/VS）
    direct_cp = {}
    uvs = {}
    for g in plan["glyphs"]:
        new_gid = old_to_new[g["glyph_index"]]
        for origin in g["origins"]:
            kind = origin["kind"]
            cp = origin.get("cp")
            if kind == "cmap" and cp is not None:
                direct_cp[cp] = new_gid
            elif kind in ("variation_non_default", "variation_default",
                          "variation_unregistered") and cp is not None:
                vs = origin.get("variation_selector")
                # 基字走 base cmap（非默认 VS glyph 通过 format14 覆盖）
                base_gid = font.cmap.get(cp)
                if base_gid is not None and base_gid in old_to_new:
                    direct_cp.setdefault(cp, old_to_new[base_gid])
                if kind == "variation_non_default":
                    uvs.setdefault(vs, []).append((cp, new_gid))
    # 确保 .notdef 不写入 cmap（gid0 不映射）
    direct_cp = {cp: gid for cp, gid in direct_cp.items() if gid != 0}
    cmap = B.build_cmap(direct_cp, uvs)

    # head：indexToLocFormat=1（long loca），其余复制后补丁
    head = _patch_head(font.sfnt.tables["head"].data)
    hhea = B.build_hhea(new_num)
    maxp = _patch_maxp(font.sfnt.tables["maxp"].data, new_num)
    post = B.build_post_format3()

    tables = {
        "cmap": cmap,
        "glyf": glyf,
        "head": head,
        "hhea": hhea,
        "hmtx": hmtx,
        "loca": loca,
        "maxp": maxp,
        "post": post,
    }
    # name 原样复制（许可证）
    if font.sfnt.has("name"):
        tables["name"] = font.sfnt.tables["name"].data
    # 可选但安全的表原样复制
    for tag in ("OS/2", "cvt ", "fpgm", "prep"):
        if font.sfnt.has(tag):
            tables[tag] = font.sfnt.tables[tag].data

    gsub, gsub_notes = _rebuild_gsub(font, plan, old_to_new)
    if gsub is not None:
        tables["GSUB"] = gsub
    return pack_sfnt(tables), old_to_new, gsub_notes


def _rebuild_glyf_record(font, gid, old_to_new):
    start = font.loca_offsets[gid]
    end = font.loca_offsets[gid + 1]
    if start == end:
        return b""
    return _patch_composite(font, gid, old_to_new)


def _patch_composite(font, gid, old_to_new):
    start = font.loca_offsets[gid]
    end = font.loca_offsets[gid + 1]
    data = bytearray(font.glyf_data[start:end])
    pos = 10
    while True:
        flags = struct.unpack_from(">H", data, pos)[0]
        struct.pack_into(
            ">H", data, pos + 2,
            old_to_new[struct.unpack_from(">H", data, pos + 2)[0]])
        pos += 4
        if flags & 0x0001:
            pos += 4
        else:
            pos += 2
        if flags & 0x0008:
            pos += 2
        elif flags & 0x0040:
            pos += 4
        elif flags & 0x0080:
            pos += 8
        if not (flags & 0x0020):
            break
    return bytes(data)


def _patch_head(head_data):
    data = bytearray(head_data)
    # indexToLocFormat offset 50
    struct.pack_into(">h", data, 50, 1)
    # checksumAdjustment 清零（pack_sfnt 会重算）
    struct.pack_into(">I", data, 8, 0)
    return bytes(data)


def _patch_maxp(maxp_data, new_num):
    data = bytearray(maxp_data)
    struct.pack_into(">H", data, 4, new_num)
    return bytes(data)


def _rebuild_gsub(font, plan, old_to_new):
    if not font.gsub:
        return None, []
    strategy = plan["strategy"]
    if strategy["feature_policy"] == "none":
        return None, ["feature_policy=none，未保留 GSUB"]
    wanted = set(strategy["features"])
    notes = []

    new_lookup_blobs = []
    # 旧 lookup index -> 新 lookup index
    lookup_remap = {}
    old_lookups = {lk.index: lk for lk in font.gsub["lookups"]}

    # 仅保留启用特性引用到的 lookup，按旧索引顺序处理
    feature_specs = []
    used_old_lookups = []
    feature_to_old = []
    for feat in font.gsub["features"]:
        if feat["tag"] not in wanted:
            continue
        kept = []
        for li in feat["lookup_indices"]:
            lk = old_lookups.get(li)
            if lk is None:
                continue
            if not lk.supported:
                notes.append("特性 %s 的 lookup %d 不受支持，已丢弃"
                             % (feat["tag"], li))
                continue
            if li not in lookup_remap:
                blob = _build_lookup_blob(lk, old_to_new, notes)
                if blob is None:
                    continue
                lookup_remap[li] = len(new_lookup_blobs)
                new_lookup_blobs.append(blob)
            if li in lookup_remap:
                kept.append(lookup_remap[li])
        if kept:
            feature_specs.append((feat["tag"], kept))
    if not new_lookup_blobs:
        return None, notes + ["没有可保留的 GSUB lookup"]
    gsub = G.assemble_gsub(feature_specs, new_lookup_blobs)
    return gsub, notes


def _remap(gid, old_to_new):
    return old_to_new.get(gid)


def _build_lookup_blob(lk, old_to_new, notes):
    try:
        if lk.lookup_type == 1:
            mapping = {}
            for st in lk.subtables:
                for src, dst in st.rule.mapping.items():
                    ns, nd = _remap(src, old_to_new), _remap(dst, old_to_new)
                    if ns is not None and nd is not None:
                        mapping[ns] = nd
            if not mapping:
                return None
            return G.lookup_blob(1, G.single_subst_format2(mapping))
        if lk.lookup_type == 2:
            mapping = {}
            for st in lk.subtables:
                for src, seq in st.rule.mapping.items():
                    ns = _remap(src, old_to_new)
                    nseq = [_remap(g, old_to_new) for g in seq]
                    if ns is not None and all(g is not None for g in nseq):
                        mapping[ns] = nseq
            if not mapping:
                return None
            return G.lookup_blob(2, G.multiple_subst(mapping))
        if lk.lookup_type == 3:
            mapping = {}
            for st in lk.subtables:
                for src, alts in st.rule.mapping.items():
                    ns = _remap(src, old_to_new)
                    nalts = [_remap(g, old_to_new) for g in alts]
                    if ns is not None and all(g is not None for g in nalts):
                        mapping[ns] = nalts
            if not mapping:
                return None
            return G.lookup_blob(3, G.alternate_subst(mapping))
        if lk.lookup_type == 4:
            ligatures = {}
            for st in lk.subtables:
                for first, entries in st.rule.ligsets.items():
                    nf = _remap(first, old_to_new)
                    if nf is None:
                        continue
                    new_entries = []
                    for comps, lig_gid in entries:
                        ncomps = [_remap(g, old_to_new) for g in comps]
                        nlig = _remap(lig_gid, old_to_new)
                        if nlig is not None and all(
                                g is not None for g in ncomps):
                            new_entries.append((ncomps, nlig))
                    if new_entries:
                        ligatures.setdefault(nf, []).extend(new_entries)
            if not ligatures:
                return None
            return G.lookup_blob(4, G.ligature_subst(ligatures))
    except Exception as exc:  # 防御：重建失败不影响主流程
        notes.append("lookup %d 重建失败：%s" % (lk.index, exc))
        return None
    return None
