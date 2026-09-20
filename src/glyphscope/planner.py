"""生成版本化、可复现的 glyph 子集计划。

计划（spec 1.0）是确定性 JSON：
  - glyph 一律按 GID 升序；
  - 每个 glyph 带有可追溯 origins（字符 / 变体选择符 / 组合组件 / 布局替代 /
    特性闭合策略 / 显式 notdef）；
  - 缺失映射（missing）与映射到 .notdef 严格区分；
  - 样本文本原样写入计划，旧计划不依赖当前存储即可重现；
  - plan_id 由字体哈希 + 计划参数 + 样本内容哈希决定。

闭合策略 closure：
  none      只保留样本直接/经布局到达的 glyph，不做特性外推；
  reachable （默认）保留特性时，把相关 lookup 上样本 glyph 能静态到达、
            但本次文本未触发的替代目标也纳入；
  full      纳入所保留特性涉及的全部替代目标。
"""

import hashlib
import json
from typing import Dict, List, Optional

from . import SPEC_VERSION
from .errors import QuarantinedFontError
from .layout import GsubEngine, GSUB_TYPE_NAMES
from .mapper import CmapIndex
from .segments import cp_label, is_whitespace, segment_text

NOTDEF_GID = 0
CLOSURE_MODES = ("none", "reachable", "full")
DEFAULT_FEATURES = ["calt", "liga", "clig", "rlig", "ccmp"]


def _sha256_obj(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sample_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_plan(
    analysis,
    raw_sha256: str,
    filename: str,
    samples: List[dict],
    features: Optional[List[str]] = None,
    closure: str = "reachable",
):
    """基于 :class:`glyphscope.loader.FontAnalysis` 构建计划。

    samples: [{"id": str(可选), "text": str}]
    """
    if getattr(analysis, "quarantined", False):
        raise QuarantinedFontError("字体已隔离，不能生成计划")
    if closure not in CLOSURE_MODES:
        raise ValueError("closure 必须是 %s 之一" % (CLOSURE_MODES,))
    features = sorted(features if features is not None else DEFAULT_FEATURES)

    order = analysis.glyph_order
    cmap = CmapIndex(analysis.font, analysis.name_to_gid)
    gsub = GsubEngine(analysis.font, order, analysis.name_to_gid)

    # origins[gid] -> list[dict]；first_kind 保留最早触发来源。
    origins: Dict[int, List[dict]] = {}
    missing = []
    notdef_refs = []
    sample_records = []
    # 布局出现过的 glyph（直接或经替代到达）。
    reached_gids = set()
    actions_all = []

    def add_origin(gid, origin):
        if gid is None:
            return
        existing = origins.setdefault(gid, [])
        for prior in existing:
            if _same_origin(prior, origin):
                return
        existing.append(origin)

    for sample in samples:
        text = sample["text"]
        sid = sample.get("id") or sample_id(text)
        clusters = segment_text(text)
        flat_input = []
        cluster_spans = []
        per_cluster = []
        for ci, cluster in enumerate(clusters):
            seq = cmap.initial_sequence(cluster)
            nodes = []
            item_notes = []
            for item in seq:
                lu = item["lookup"]
                cp = item["cp"]
                vs = item["vs"]
                if lu["status"] == "missing":
                    if not is_whitespace(cp):
                        missing.append(
                            {
                                "sample_id": sid,
                                "cluster": ci,
                                "cp": cp,
                                "cp_label": cp_label(cp),
                                "variation_selector": ("U+%X" % vs) if vs else None,
                                "reason": "cmap 无映射"
                                + (
                                    "（变体选择符未覆盖）"
                                    if lu.get("variant_uncovered")
                                    else ""
                                ),
                            }
                        )
                    nodes.append({"gid": None, "cps": [cp], "kind": item["kind"], "history": []})
                    item_notes.append(
                        {"cp": cp, "status": "skipped" if is_whitespace(cp) else "missing"}
                    )
                    continue
                origin = {
                    "kind": "variation" if vs is not None else (
                        "combining" if item["kind"] == "mark" else "character"
                    ),
                    "cp": cp,
                    "cp_label": cp_label(cp),
                    "sample_id": sid,
                }
                if vs is not None:
                    origin["variation_selector"] = "U+%X" % vs
                    if lu.get("variant_default_uvs"):
                        origin["variant_default_uvs"] = True
                if lu["status"] == "notdef":
                    notdef_refs.append(
                        {
                            "sample_id": sid,
                            "cluster": ci,
                            "cp": cp,
                            "cp_label": cp_label(cp),
                            "reason": "映射到 GID 0 (.notdef)",
                        }
                    )
                    nodes.append({"gid": 0, "cps": [cp], "kind": item["kind"], "history": []})
                    add_origin(0, dict(origin, kind="mapped_notdef"))
                    item_notes.append({"cp": cp, "status": "notdef"})
                    continue
                gid = lu["gid"]
                nodes.append({"gid": gid, "cps": [cp], "kind": item["kind"], "history": []})
                add_origin(gid, origin)
                reached_gids.add(gid)
                item_notes.append({"cp": cp, "status": "mapped", "gid": gid})
            start = len(flat_input)
            flat_input.extend(nodes)
            cluster_spans.append((ci, start, start + len(nodes), item_notes, cluster))

        shaped, actions = gsub.shape(flat_input, features)
        for action in actions:
            action_out = dict(action)
            action_out["sample_id"] = sid
            actions_all.append(action_out)
            for gid in action["output_gids"]:
                reached_gids.add(gid)
                add_origin(
                    gid,
                    {
                        "kind": "layout",
                        "rule": action["detail"].get("rule"),
                        "feature": action["feature"],
                        "lookup_index": action["lookup_index"],
                        "input_gids": action["input_gids"],
                        "cps": action["cps"],
                        "sample_id": sid,
                    },
                )

        # 记录每个簇塑形结果，用于本地预览。
        shaped_index = 0
        shaped_clusters = []
        for ci, start, end, notes, cluster in cluster_spans:
            out_nodes = shaped[start:end] if end <= len(shaped) else shaped[start:]
            shaped_clusters.append(
                {
                    "cluster_index": ci,
                    "text": cluster.text,
                    "base_cp": cluster.base,
                    "base_label": cluster.base_label,
                    "variation_selector": (
                        "U+%X" % cluster.variation_selector
                        if cluster.variation_selector
                        else None
                    ),
                    "input": notes,
                    "output_gids": [n["gid"] for n in out_nodes],
                    "output_names": [
                        order[n["gid"]] if n["gid"] is not None else None
                        for n in out_nodes
                    ],
                    "actions": [
                        a for a in actions
                        if any(cp in a["cps"] for cp in cluster.chars)
                    ],
                }
            )
        sample_records.append(
            {
                "id": sid,
                "text": text,
                "num_clusters": len(clusters),
                "clusters": shaped_clusters,
            }
        )

    # 复合 glyph 依赖：传递闭包（加载时已保证无环），记录组件溯源边。
    transitive = getattr(analysis.composite, "transitive", {})
    direct_edges = analysis.composite.edges
    for gid in sorted(reached_gids):
        for dep in transitive.get(gid, []):
            chain = _component_chain(gid, dep, direct_edges)
            add_origin(
                dep,
                {
                    "kind": "component",
                    "parent_gid": gid,
                    "parent_name": order[gid],
                    "chain_gids": chain,
                    "via": [order[g] for g in chain],
                },
            )

    candidate_gids = set(origins.keys())

    # 特性闭合策略：把保留特性相关、但样本未触发的 glyph 按策略纳入。
    closure_added, closure_notes = _apply_closure(
        gsub, features, closure, reached_gids, candidate_gids, add_origin
    )

    # .notdef 始终保留（字体规范要求 GID0 存在），若无来源则标注策略保留。
    if NOTDEF_GID not in origins:
        add_origin(
            NOTDEF_GID,
            {"kind": "required", "reason": "GID 0 (.notdef) 规范要求保留"},
        )
    candidate_gids = set(origins.keys())

    glyphs = []
    for gid in sorted(candidate_gids):
        glyphs.append(
            {
                "gid": gid,
                "name": order[gid] if gid < len(order) else None,
                "is_notdef": gid == NOTDEF_GID,
                "components": list(direct_edges.get(gid, [])),
                "origins": origins[gid],
            }
        )

    stats = {
        "num_glyphs_in_font": analysis.num_glyphs,
        "num_candidate_glyphs": len(glyphs),
        "num_missing": len(missing),
        "num_notdef_refs": len(notdef_refs),
        "num_layout_actions": len(actions_all),
        "num_closure_added": len(closure_added),
    }

    samples_fingerprint = _sha256_obj(
        [{"id": s.get("id") or sample_id(s["text"]), "text": s["text"]} for s in samples]
    )
    params = {
        "features": features,
        "closure": closure,
        "samples_fingerprint": samples_fingerprint,
    }
    plan = {
        "spec_version": SPEC_VERSION,
        "font": {
            "filename": filename,
            "sha256": raw_sha256,
            "flavor": analysis.flavor,
            "num_glyphs": analysis.num_glyphs,
            "has_gsub": analysis.has_gsub,
            "has_gpos": analysis.has_gpos,
            "has_glyf": analysis.has_glyf,
            "has_cff": analysis.has_cff,
            "license": analysis.licenses,
        },
        "parameters": params,
        "samples": [
            {"id": r["id"], "text": r["text"], "num_clusters": r["num_clusters"]}
            for r in sample_records
        ],
        "glyphs": glyphs,
        "missing": missing,
        "notdef_refs": notdef_refs,
        "layout_actions": actions_all,
        "layout_summary": gsub.summarize(),
        "sample_shaping": sample_records,
        "closure": {"mode": closure, "added_gids": sorted(closure_added),
                    "notes": closure_notes},
        "stats": stats,
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def _same_origin(a, b) -> bool:
    keys = (
        "kind", "cp", "variation_selector", "parent_gid", "feature",
        "lookup_index", "rule", "sample_id", "reason",
    )
    return all(a.get(k) == b.get(k) for k in keys) and (
        a.get("input_gids") == b.get("input_gids") if "input_gids" in a or "input_gids" in b else True
    )


def _component_chain(src, dst, edges):
    """返回 src -> ... -> dst 的一条直接组件路径（DAG 上 BFS）。"""
    from collections import deque

    queue = deque([[src]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == dst:
            return path
        for child in edges.get(node, []):
            if child not in path:
                queue.append(path + [child])
    return [src, dst]


def _apply_closure(gsub, features, mode, reached, candidates, add_origin):
    notes = []
    added = set()
    if mode == "none":
        return added, notes

    plan = gsub.feature_plan(features)
    lookup_indices = sorted({li for _tag, li in plan})
    for li in lookup_indices:
        edges = gsub.lookup_edges(li)
        ltype_name = GSUB_TYPE_NAMES.get(gsub.lookup_type(li), "lookup")
        if mode == "full":
            for src, dst, meta in edges:
                if dst not in candidates:
                    added.add(dst)
                    add_origin(
                        dst,
                        {
                            "kind": "closure",
                            "policy": "full",
                            "lookup_index": li,
                            "lookup_type": ltype_name,
                            "rule": meta.get("rule"),
                            "feature": _tag_for_lookup(plan, li),
                        },
                    )
            continue
        # reachable：从“样本到达 glyph 集合”出发沿 lookup 边扩展。
        frontier = set(reached) | set(candidates)
        changed = True
        while changed:
            changed = False
            for src, dst, meta in edges:
                if src in frontier and dst not in frontier and dst not in added:
                    added.add(dst)
                    frontier.add(dst)
                    changed = True
                    add_origin(
                        dst,
                        {
                            "kind": "closure",
                            "policy": "reachable",
                            "lookup_index": li,
                            "lookup_type": ltype_name,
                            "rule": meta.get("rule"),
                            "source_gid": src,
                            "feature": _tag_for_lookup(plan, li),
                        },
                    )
    return added, notes


def _tag_for_lookup(plan, li):
    tags = sorted({tag for tag, idx in plan if idx == li})
    return tags[0] if tags else None


def _plan_id(plan: dict) -> str:
    key = {
        "spec": plan["spec_version"],
        "font_sha256": plan["font"]["sha256"],
        "parameters": plan["parameters"],
        "samples": plan["samples"],
    }
    return hashlib.sha256(
        json.dumps(key, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
