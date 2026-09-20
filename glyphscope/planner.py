"""生成候选 glyph 集合并把每个 glyph 追溯到触发来源。"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import glyf as glyf_mod
from . import unicode_help as uh

POLICY_NONE = "none"
POLICY_BASE = "base"
POLICY_CLOSURE = "closure"
DEFAULT_FEATURES = ["ccmp", "liga", "rlig", "clig", "calt"]


@dataclass
class SampleRun:
    text: str
    label: str = ""


@dataclass
class PlanOptions:
    feature_policy: str = POLICY_CLOSURE
    features: List[str] = field(default_factory=lambda: list(DEFAULT_FEATURES))
    include_notdef: bool = False
    apply_alternates: bool = False
    compose_combining: bool = True


@dataclass
class Trace:
    glyph_index: int
    glyph_name: str
    origins: List[Dict[str, Any]] = field(default_factory=list)
    included_by: List[str] = field(default_factory=list)


def tokenize_samples(runs):
    """把文本样本拆成可展示的聚类（cluster）。

    每个 cluster 记录原始字符、codepoints、base_cp、variation_selector。
    """
    clusters = []
    for run in runs:
        text = run.text
        i = 0
        while i < len(text):
            ch = text[i]
            cp = ord(ch)
            nxt_cp = ord(text[i + 1]) if i + 1 < len(text) else None
            if nxt_cp is not None and uh.is_variation_selector(nxt_cp):
                clusters.append({
                    "chars": ch + text[i + 1],
                    "codepoints": [cp, nxt_cp],
                    "base_cp": cp,
                    "variation_selector": nxt_cp,
                    "combining": False,
                    "sample_label": run.label,
                })
                i += 2
                continue
            if uh.is_combining_mark(cp):
                # 挂到前一个非组合 cluster（同一 run 内）
                if clusters and clusters[-1].get("sample_label") == run.label \
                        and not clusters[-1].get("trailing_mark"):
                    prev = clusters[-1]
                    prev["chars"] += ch
                    prev["codepoints"].append(cp)
                    prev.setdefault("mark_cps", []).append(cp)
                    i += 1
                    continue
                clusters.append({
                    "chars": ch,
                    "codepoints": [cp],
                    "base_cp": None,
                    "variation_selector": None,
                    "combining": True,
                    "trailing_mark": True,
                    "sample_label": run.label,
                })
                i += 1
                continue
            clusters.append({
                "chars": ch,
                "codepoints": [cp],
                "base_cp": cp,
                "variation_selector": None,
                "combining": False,
                "sample_label": run.label,
            })
            i += 1
    return clusters


def _origin(kind, **kw):
    d = {"kind": kind}
    d.update(kw)
    return d


def _resolve_feature_lookups(font, options):
    """返回启用的 lookup index 列表，按字体顺序去重。"""
    if not font.gsub or options.feature_policy == POLICY_NONE:
        return [], []
    wanted = set(options.features) if options.features else set()
    enabled = []
    enabled_feature_tags = []
    for feat in font.gsub["features"]:
        if feat["tag"] in wanted:
            enabled_feature_tags.append(feat["tag"])
            for li in feat["lookup_indices"]:
                if li not in enabled:
                    enabled.append(li)
    enabled.sort()
    return enabled, enabled_feature_tags


def shape(font, clusters, options):
    """在全局 glyph 流上做最小 shaping（连字可跨字符聚类）。

    返回 (tokens, enabled_feature_tags)。每个 token 对应一个输入 cluster，
    记录最终 glyph、缺失/.notdef 状态与替代链。
    """
    lookup_indices, feature_tags = _resolve_feature_lookups(font, options)
    lookups_by_index = {lk.index: lk for lk in font.gsub["lookups"]} \
        if font.gsub else {}
    feature_by_lookup = {}
    if font.gsub:
        for feat in font.gsub["features"]:
            for li in feat["lookup_indices"]:
                feature_by_lookup.setdefault(li, feat["tag"])

    tokens = []
    # 全局流：item = {gid, token_index, cp, vs, vs_kind, active}
    stream = []
    for idx, cluster in enumerate(clusters):
        token = {
            "chars": cluster["chars"],
            "codepoints": list(cluster["codepoints"]),
            "base_cp": cluster.get("base_cp"),
            "variation_selector": cluster.get("variation_selector"),
            "mark_cps": list(cluster.get("mark_cps", [])),
            "sample_label": cluster.get("sample_label", ""),
            "glyphs": [],
            "status": "mapped",
            "reasons": [],
            "substitution_chain": [],
            "resolved": [],
        }
        resolved = []
        base_cp = cluster.get("base_cp")
        vs = cluster.get("variation_selector")
        if base_cp is not None:
            if vs is not None:
                gid, vs_kind = font.variation_glyph(base_cp, vs)
                if vs_kind == "none":
                    gid = font.cmap.get(base_cp)
                    vs_kind = "unregistered"
            else:
                gid = font.cmap.get(base_cp)
                vs_kind = None
            resolved.append({"cp": base_cp, "gid": gid,
                             "vs": vs, "vs_kind": vs_kind})
        for cp in cluster.get("mark_cps", []):
            resolved.append({"cp": cp, "gid": font.cmap.get(cp),
                             "vs": None, "vs_kind": None})

        for item in resolved:
            token["reasons"].append(_origin_for(item))
            stream.append({
                "gid": item["gid"],
                "token_index": idx,
                "cp": item["cp"],
                "vs": item.get("vs"),
                "vs_kind": item.get("vs_kind"),
            })
        if cluster.get("mark_cps") and options.compose_combining:
            token["reasons"].append(_origin(
                "combining_sequence", base_cp=base_cp,
                marks=[uh.codepoint_label(c)
                       for c in cluster["mark_cps"]]))
        token["resolved"] = resolved
        tokens.append(token)

    stream, chains = _apply_lookups_stream(
        font, stream, lookup_indices, lookups_by_index,
        feature_by_lookup, options)

    # 把替代链归档到对应 token
    for step in chains:
        ti = step["token_index"]
        tokens[ti]["substitution_chain"].append(step)
        tokens[ti]["reasons"].append(_origin(
            step["kind"], lookup_index=step.get("lookup_index"),
            feature=step.get("feature_tag"),
            from_glyphs=step.get("from"),
            to_glyphs=step.get("to")))

    # 连字已经把多个流位置合并为一个代表位置（token_index 取首字符）。
    # 最终 glyph 按代表位置归属；被吸收的输入 token 记录 consumed 标记。
    for idx, token in enumerate(tokens):
        items = [it for it in stream if it["token_index"] == idx]
        gids = [it["gid"] for it in items]
        token["glyphs"] = gids
        token["absorbed"] = len(items) == 0
        missing = any(o["kind"] == "missing_mapping" for o in token["reasons"])
        if token["absorbed"]:
            token["status"] = "absorbed"
        elif missing:
            token["status"] = "missing"
        elif any(g == 0 for g in gids if g is not None):
            token["status"] = "notdef"
        else:
            token["status"] = "mapped"
    return tokens, feature_tags


def _absorbed_by_ligature(chains, token_index):
    """该 token 是否作为非首组件被某连字吸收（最终流不再有其 glyph）。"""
    # 简化判定：存在以该 token_index 之后位置为组件的连字链时，
    # 由调用数据保证；这里通过 chain 中无法直接得知，故 stream 合并时
    # 已把非首 token 从流中移除，items 为空即视为 absorbed。
    return False


def _origin_for(item):
    cp = item["cp"]
    if item.get("gid") is None:
        return _origin("missing_mapping", cp=cp,
                       cp_label=uh.codepoint_label(cp),
                       variation_selector=item.get("vs"))
    kind = "cmap"
    detail = {"cp": cp, "cp_label": uh.codepoint_label(cp)}
    vs = item.get("vs")
    if vs is not None:
        kind = {
            "non_default": "variation_non_default",
            "default": "variation_default",
            "unregistered": "variation_unregistered",
        }.get(item.get("vs_kind"), "variation")
        detail["variation_selector"] = vs
        detail["variation_selector_label"] = uh.codepoint_label(vs)
    return _origin(kind, **detail)


def _apply_lookups_stream(font, stream, lookup_indices,
                           lookups_by_index, feature_by_lookup, options):
    chains = []
    for li in lookup_indices:
        lk = lookups_by_index.get(li)
        if lk is None or not lk.supported:
            continue
        ftag = feature_by_lookup.get(li, "")
        if lk.lookup_type == 1:
            for item in stream:
                gid = item["gid"]
                if gid is None:
                    continue
                for st in lk.subtables:
                    if gid in st.rule.mapping:
                        out = st.rule.mapping[gid]
                        chains.append(_chain(
                            "layout_single", li, ftag,
                            item["token_index"], [gid], [out]))
                        item["gid"] = out
                        break
        elif lk.lookup_type == 2:
            new_stream = []
            for item in stream:
                gid = item["gid"]
                expanded = None
                if gid is not None:
                    for st in lk.subtables:
                        if gid in st.rule.mapping:
                            expanded = st.rule.mapping[gid]
                            break
                if expanded is None:
                    new_stream.append(item)
                    continue
                chains.append(_chain(
                    "layout_multiple", li, ftag,
                    item["token_index"], [gid], list(expanded)))
                first = dict(item)
                first["gid"] = expanded[0]
                new_stream.append(first)
                for out in expanded[1:]:
                    clone = dict(item)
                    clone["gid"] = out
                    new_stream.append(clone)
            stream = new_stream
        elif lk.lookup_type == 3:
            if not options.apply_alternates:
                continue
            for item in stream:
                gid = item["gid"]
                if gid is None:
                    continue
                for st in lk.subtables:
                    if gid in st.rule.mapping and st.rule.mapping[gid]:
                        out = st.rule.mapping[gid][0]
                        chains.append(_chain(
                            "layout_alternate", li, ftag,
                            item["token_index"], [gid], [out]))
                        item["gid"] = out
                        break
        elif lk.lookup_type == 4:
            stream = _apply_ligatures_stream(
                stream, lk, li, ftag, chains)
    return stream, chains


def _apply_ligatures_stream(stream, lk, li, ftag, chains):
    result = []
    i = 0
    n = len(stream)
    while i < n:
        item = stream[i]
        gid = item["gid"]
        candidates = []
        if gid is not None:
            for st in lk.subtables:
                if gid in st.rule.ligsets:
                    candidates = st.rule.ligsets[gid]
                    break
        best = None
        if candidates and i + 1 < n:
            for components, lig_gid in candidates:
                window = [stream[i + 1 + k]["gid"]
                          for k in range(len(components))]
                if len(window) == len(components) and window == components:
                    if best is None or len(components) > len(best[0]):
                        best = (components, lig_gid)
        if best is not None:
            components, lig_gid = best
            consumed_items = stream[i:i + 1 + len(components)]
            consumed_gids = [it["gid"] for it in consumed_items]
            token_index = item["token_index"]
            chains.append(_chain(
                "layout_ligature", li, ftag, token_index,
                list(consumed_gids), [lig_gid]))
            merged = dict(item)
            merged["gid"] = lig_gid
            merged["merged_token_indices"] = sorted({
                it["token_index"] for it in consumed_items})
            result.append(merged)
            i += 1 + len(components)
        else:
            result.append(item)
            i += 1
    return result


def _chain(kind, li, ftag, token_index, from_gids, to_gids):
    return {
        "kind": kind,
        "lookup_index": li,
        "feature_tag": ftag,
        "token_index": token_index,
        "from": list(from_gids),
        "to": list(to_gids),
    }


def build_plan(font, runs, options=None):
    options = options or PlanOptions()
    if options.feature_policy not in (POLICY_NONE, POLICY_BASE, POLICY_CLOSURE):
        raise ValueError("非法 feature_policy: %s" % options.feature_policy)

    clusters = tokenize_samples(runs)
    tokens, enabled_features = shape(font, clusters, options)

    traces = {}
    edges = []

    def add_glyph(gid, origin=None, included_by=None):
        if gid is None:
            return
        tr = traces.get(gid)
        if tr is None:
            info = font.glyphs[gid] if 0 <= gid < font.num_glyphs else None
            tr = Trace(gid, info.name if info else "gid%d" % gid)
            traces[gid] = tr
        if origin is not None and origin not in tr.origins:
            tr.origins.append(origin)
        if included_by and included_by not in tr.included_by:
            tr.included_by.append(included_by)

    def add_edge(edge):
        if edge not in edges:
            edges.append(edge)

    triggered = set()
    for token in tokens:
        for gid in token["glyphs"]:
            if gid is not None:
                triggered.add(gid)

        # 字符 -> glyph（cmap / VS）
        for item in token.get("resolved", []):
            gid = item["gid"]
            cp = item["cp"]
            vs = item.get("vs")
            if gid is not None:
                kind = "cmap"
                if vs is not None:
                    kind = {
                        "non_default": "variation_non_default",
                        "default": "variation_default",
                        "unregistered": "variation_unregistered",
                    }.get(item.get("vs_kind"), "variation")
                add_glyph(gid, {
                    "kind": kind,
                    "char": chr(cp),
                    "cp": cp,
                    "cp_label": uh.codepoint_label(cp),
                    "variation_selector": vs,
                    "variation_selector_label": (
                        uh.codepoint_label(vs) if vs else None),
                    "sample_label": token["sample_label"],
                }, included_by="text")
                add_edge({
                    "source_type": "char",
                    "source_cp": cp,
                    "variation_selector": vs,
                    "target_gid": gid,
                    "kind": kind,
                    "sample_label": token["sample_label"],
                })
                # 非默认 UVS：基字默认 glyph 仍是字体依赖（默认回退形态），
                # 单独记录为 variation_base_dependency。
                if kind == "variation_non_default":
                    base_gid = font.cmap.get(cp)
                    if base_gid is not None and base_gid != gid:
                        add_glyph(base_gid, {
                            "kind": "variation_base_dependency",
                            "char": chr(cp),
                            "cp": cp,
                            "cp_label": uh.codepoint_label(cp),
                            "variation_selector": vs,
                            "sample_label": token["sample_label"],
                        }, included_by="variation_base")
                        add_edge({
                            "source_type": "char",
                            "source_cp": cp,
                            "variation_selector": vs,
                            "target_gid": base_gid,
                            "kind": "variation_base_dependency",
                            "sample_label": token["sample_label"],
                        })

        # 布局替代关系（glyph -> glyph）
        for step in token["substitution_chain"]:
            for gid in step["to"]:
                add_glyph(gid, {
                    "kind": step["kind"],
                    "feature": step["feature_tag"],
                    "lookup_index": step["lookup_index"],
                    "from_glyphs": list(step["from"]),
                    "sample_label": token["sample_label"],
                }, included_by="layout")
            for src_gid in step["from"]:
                for dst_gid in step["to"]:
                    add_edge({
                        "source_type": "glyph",
                        "source_gid": src_gid,
                        "target_gid": dst_gid,
                        "kind": step["kind"],
                        "feature": step["feature_tag"],
                        "lookup_index": step["lookup_index"],
                    })

    # 2) 复合 glyph 依赖传递闭合（加载时已证明无环）
    with_components = glyf_mod.transitive_components(
        font.glyphs, triggered)
    for gid in sorted(triggered):
        info = font.glyphs[gid]
        if info.is_composite:
            for part in info.components:
                add_edge({
                    "source_type": "glyph",
                    "source_gid": gid,
                    "target_gid": part.glyph_index,
                    "kind": "composite_component",
                })
    for gid in sorted(with_components - triggered):
        parent = _component_parent(font, gid, triggered)
        add_glyph(gid, {
            "kind": "composite_dependency",
            "parent_glyph": parent,
            "parent_name": (font.glyphs[parent].name
                            if parent is not None else None),
        }, included_by="composite")
        if parent is not None:
            add_edge({
                "source_type": "glyph",
                "source_gid": parent,
                "target_gid": gid,
                "kind": "composite_component",
                "transitive": parent not in triggered,
            })

    # 3) 布局特性纳入策略
    feature_lookups, _ = _resolve_feature_lookups(font, options)
    extra_layout = set()
    dropped_warnings = []
    if options.feature_policy in (POLICY_BASE, POLICY_CLOSURE) and font.gsub:
        wanted = set(options.features) if options.features else set()
        by_index = {lk.index: lk for lk in font.gsub["lookups"]}
        feature_tags = [f["tag"] for f in font.gsub["features"]
                        if f["tag"] in wanted]
        for li in feature_lookups:
            lk = by_index[li]
            if not lk.supported:
                dropped_warnings.append(
                    "lookup %d: %s（导出时丢弃）" %
                    (li, lk.dropped_reason or "不支持"))
                continue
            if options.feature_policy == POLICY_BASE:
                continue
            closure_gids = _lookup_closure_gids(lk, triggered)
            for gid in closure_gids:
                if gid not in with_components:
                    extra_layout.add((gid, li))

    for gid, li in sorted(extra_layout):
        add_glyph(gid, {
            "kind": "feature_closure",
            "lookup_index": li,
            "feature": _feature_for_lookup(font, li, options),
        }, included_by="feature_closure")

    # 4) .notdef 始终保留为子集 gid0（TrueType 子集约定）；
    # include_notdef 额外显式记录策略来源
    if 0 < font.num_glyphs:
        add_glyph(0, {"kind": "required_notdef"}, included_by="required")
        if options.include_notdef:
            add_glyph(0, {"kind": "policy_include_notdef"},
                      included_by="policy")

    # 5) 所有纳入 glyph 的复合依赖再闭合一次（feature glyph 也可能是复合）
    all_seed = set(traces)
    full_closure = glyf_mod.transitive_components(font.glyphs, all_seed)
    for gid in sorted(full_closure - set(traces)):
        parent = _nearest_parent(font, gid, set(traces))
        add_glyph(gid, {
            "kind": "composite_dependency",
            "parent_glyph": parent,
            "parent_name": (font.glyphs[parent].name
                            if parent is not None else None),
        }, included_by="composite")

    return {
        "_traces": traces,
        "_edges": edges,
        "dropped_warnings": dropped_warnings,
    }


def _gid_for_cp(token, cp):
    for item in token.get("resolved", []):
        if item["cp"] == cp:
            return item["gid"]
    return None


def _lookup_closure_gids(lk, triggered):
    """closure 策略：保证被保留特性在子集内仍可工作所需的全部 glyph。

    - single：coverage 输入 + 全部替换目标
    - multiple/alternate：coverage 输入 + 全部序列/替代 glyph
    - ligature：coverage 首 glyph + 全部连字组件 + 全部连字结果
    """
    gids = set()
    for st in lk.subtables:
        rule = st.rule
        gids.update(st.coverage)
        if rule.kind == "single":
            gids.update(rule.mapping.values())
        elif rule.kind in ("multiple", "alternate"):
            for seq in rule.mapping.values():
                gids.update(seq)
        elif rule.kind == "ligature":
            for first, entries in rule.ligsets.items():
                gids.add(first)
                for components, lig_gid in entries:
                    gids.update(components)
                    gids.add(lig_gid)
    return gids


def _feature_for_lookup(font, li, options):
    wanted = set(options.features) if options.features else set()
    for feat in font.gsub["features"]:
        if feat["tag"] in wanted and li in feat["lookup_indices"]:
            return feat["tag"]
    return None


def _component_parent(font, gid, triggered):
    for parent in font.glyphs:
        if parent.is_composite:
            for part in parent.components:
                if part.glyph_index == gid and parent.index in triggered:
                    return parent.index
    return _nearest_parent(font, gid, triggered)


def _nearest_parent(font, gid, seed):
    stack = list(seed)
    seen = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        info = font.glyphs[cur]
        if info.is_composite:
            for part in info.components:
                if part.glyph_index == gid:
                    return cur
                if part.glyph_index not in seen:
                    stack.append(part.glyph_index)
    return None


def _char_for_cp(font, cp, vs=None):
    ch = chr(cp) if cp is not None else ""
    if vs:
        ch += chr(vs)
    return ch


def _glyph_record(font, gid, trace):
    info = font.glyphs[gid]
    return {
        "glyph_index": gid,
        "name": trace.glyph_name,
        "included_by": list(trace.included_by),
        "is_composite": info.is_composite,
        "components": [
            {"glyph_index": p.glyph_index,
             "name": font.glyphs[p.glyph_index].name}
            for p in info.components
        ],
        "advance": info.advance,
        "x_min": info.x_min, "y_min": info.y_min,
        "x_max": info.x_max, "y_max": info.y_max,
        "origins": list(trace.origins),
    }


def summarize_plan(font, runs, options, tokens, traces, edges,
                   enabled_features, dropped_warnings):
    ordered_gids = sorted(traces)
    glyphs = [_glyph_record(font, gid, traces[gid]) for gid in ordered_gids]

    missing = []
    notdef_tokens = []
    for token in tokens:
        label = {"chars": token["chars"],
                 "codepoints": token["codepoints"],
                 "sample_label": token["sample_label"]}
        if token["status"] == "missing":
            missing.append(label)
        elif token["status"] == "notdef":
            notdef_tokens.append(label)

    # 确定性排序：按 gid
    glyphs.sort(key=lambda g: g["glyph_index"])

    samples = [{"label": r.label, "text": r.text} for r in runs]
    strategy = {
        "feature_policy": options.feature_policy,
        "features": list(options.features),
        "enabled_features": enabled_features,
        "include_notdef": options.include_notdef,
        "apply_alternates": options.apply_alternates,
        "compose_combining": options.compose_combining,
    }
    stats = {
        "num_glyphs_in_font": font.num_glyphs,
        "num_glyphs_in_subset": len(glyphs),
        "num_composite_glyphs": sum(1 for g in glyphs if g["is_composite"]),
        "num_missing_chars": len(missing),
        "num_notdef_chars": len(notdef_tokens),
        "num_layout_substitutions": sum(
            len(t["substitution_chain"]) for t in tokens),
    }
    return samples, strategy, glyphs, missing, notdef_tokens, stats, edges


def make_plan(font, runs, options=None, created_at=None):
    options = options or PlanOptions()
    clusters = tokenize_samples(runs)
    tokens, enabled_features = shape(font, clusters, options)

    # 复用 build_plan 收集 traces/edges
    plan = build_plan(font, runs, options)
    traces = plan["_traces"]
    edges = plan["_edges"]

    glyph_records, missing, notdef_tokens, stats, edges_out = None, None, None, None, None
    (samples, strategy, glyph_records, missing, notdef_tokens,
     stats, edges_out) = summarize_plan(
        font, runs, options, tokens, traces, edges,
        enabled_features, plan["dropped_warnings"])

    body = {
        "format": "glyphscope-plan",
        "format_version": 1,
        "font": {
            "family": font.meta.family,
            "table_digest": font.digest,
            "num_glyphs": font.num_glyphs,
            "units_per_em": font.meta.units_per_em,
            "license_name": font.names.get(13) if False else
            font.meta.license_text,
            "license_url": font.meta.license_url,
        },
        "samples": samples,
        "strategy": strategy,
        "glyphs": glyph_records,
        "edges": edges_out,
        "missing": missing,
        "notdef": notdef_tokens,
        "stats": stats,
        "preview_tokens": [_preview_token(font, t) for t in tokens],
        "dropped_warnings": plan["dropped_warnings"],
    }
    content_hash = content_fingerprint(body)
    body["content_hash"] = content_hash
    body["created_at"] = created_at or _now_iso()
    return body


def _preview_token(font, token):
    gids = [g for g in token["glyphs"] if g is not None]
    return {
        "chars": token["chars"],
        "codepoints": token["codepoints"],
        "status": token["status"],
        "glyphs": gids,
        "glyph_names": [font.glyphs[g].name for g in gids],
        "reasons": token["reasons"],
        "substitution_chain": token["substitution_chain"],
        "sample_label": token["sample_label"],
    }


def content_fingerprint(body):
    """对影响结果的输入做规范化哈希（不含 created_at / version 号）。"""
    key = {
        "format": body["format"],
        "format_version": body["format_version"],
        "font_digest": body["font"]["table_digest"],
        "samples": body["samples"],
        "strategy": body["strategy"],
        "glyph_gids": [g["glyph_index"] for g in body["glyphs"]],
    }
    blob = json.dumps(key, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
