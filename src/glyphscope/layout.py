"""确定性、可解释的 GSUB 子集模拟器。

支持类型：1 SingleSubst、2 MultipleSubst、4 LigatureSubst、
5 ContextSubst(1/2/3)、6 ChainContextSubst(1/2/3)、
8 ReverseChainSingle(1)、7 Extension(自动解包)。
类型 3 Alternate 需要交互选择，不参与模拟，仅摘要计数。

所有 Coverage/ClassDef 在构造时转成 GID 口径；模拟过程中的
每次替代都会产出溯源动作；同时提供 lookup 静态“可达边”，
供特性闭合策略（保留特性但样本文本未直接触发）使用。
"""

from collections import OrderedDict
from typing import Dict, List, Tuple

GSUB_TYPE_NAMES = {
    1: "single",
    2: "multiple",
    3: "alternate",
    4: "ligature",
    5: "context",
    6: "chained_context",
    7: "extension",
    8: "reverse_chaining_single",
    9: "extension",
}

DEFAULT_FEATURE_ORDER = [
    "ccmp", "locl", "rlig", "liga", "clig", "calt", "rclt",
    "kern", "mark", "mkmk",
]
MAX_ACTIONS = 4096
SIM_PASSES = 3



def _is_expanding_noop(kind, consumed, out_gids, buf, pos):
    if kind != "multiple":
        return False
    in_gids = [n["gid"] for n in buf[pos : pos + consumed]]
    return out_gids[: len(in_gids)] == in_gids and len(out_gids) >= len(in_gids)


def _node_seen_lookup(node, tag, lookup_idx):
    base_tag = tag.split(">")[0]
    for action in node.get("history", []):
        if (
            action["lookup_index"] == lookup_idx
            and action["feature"].split(">")[0] == base_tag
        ):
            return True
    return False


def gsub_type_of(st) -> int:
    name = type(st).__name__
    table = [
        ("SingleSubst", 1),
        ("MultipleSubst", 2),
        ("AlternateSubst", 3),
        ("LigatureSubst", 4),
        ("ContextSubst", 5),
        ("ChainContextSubst", 6),
        ("ReverseChainSingleSubst", 8),
    ]
    for prefix, val in table:
        if name.startswith(prefix):
            return val
    return 0


def format_of(st) -> int:
    return int(getattr(st, "Format", 0) or 0)


class GsubEngine:
    """基于 GID 的 GSUB 引擎。"""

    def __init__(self, font, order: List[str], name_to_gid: Dict[str, int]):
        self.font = font
        self.order = order
        self.name_to_gid = name_to_gid
        self.gsub = font.get("GSUB")
        self.gpos = font.get("GPOS")
        self.lookups = []
        if self.gsub is not None and getattr(self.gsub, "table", None) is not None:
            if self.gsub.table.LookupList is not None:
                self.lookups = list(self.gsub.table.LookupList.Lookup)
        self._annotate_all()

    # ---------- 预处理：Coverage/ClassDef -> GID ----------

    def _gid_set(self, coverage) -> set:
        if coverage is None:
            return set()
        return {
            self.name_to_gid[n]
            for n in (getattr(coverage, "glyphs", None) or [])
            if n in self.name_to_gid
        }

    def _class_map(self, classdef) -> Dict[int, int]:
        if classdef is None:
            return {}
        out = {}
        for gname, cls in (getattr(classdef, "classDefs", None) or {}).items():
            gid = self.name_to_gid.get(gname) if isinstance(gname, str) else None
            if gid is not None:
                out[gid] = cls
        return out

    def _annotate_all(self):
        for lookup in self.lookups:
            for st in lookup.SubTable:
                self._annotate(st, 0)

    def _annotate(self, obj, depth: int):
        if depth > 14 or obj is None:
            return
        ext = getattr(obj, "extSubTable", None)
        if ext is not None:
            self._annotate(ext, depth + 1)
        for attr in (
            "Coverage",
            "InputCoverage",
            "BacktrackCoverage",
            "LookAheadCoverage",
        ):
            val = getattr(obj, attr, None)
            if val is None:
                continue
            # format3 的输入 Coverage 在 fontTools 里是名为 Coverage 的列表；
            # 单表的起始 Coverage 是单个对象。
            convs = val if isinstance(val, list) else [val]
            for cov in convs:
                if cov is not None and not hasattr(cov, "_gs_gid_set"):
                    cov._gs_gid_set = self._gid_set(cov)
        for attr in (
            "ClassDef",
            "InputClassDef",
            "BacktrackClassDef",
            "LookAheadClassDef",
        ):
            cdef = getattr(obj, attr, None)
            if cdef is not None and not hasattr(cdef, "_gs_class_map"):
                cdef._gs_class_map = self._class_map(cdef)
        for attr in (
            "SubRuleSet", "SubClassSet", "ChainSubRuleSet", "ChainSubClassSet",
        ):
            val = getattr(obj, attr, None)
            if val is None:
                continue
            for rset in val:
                if rset is None:
                    continue
                for r in (getattr(rset, "SubRule", None) or []) + (
                    getattr(rset, "SubClassRule", None) or []
                ) + (getattr(rset, "ChainSubRule", None) or []):
                    self._annotate(r, depth + 1)
                # 规则里也可能直接带 Coverage（format3 在 st 本体，规则内一般无）。
        # format3 的上下文表自身即规则容器。
        for attr in ("SubstLookupRecord",):
            for rec in getattr(obj, attr, None) or []:
                self._annotate(getattr(rec, "SubTable", None), depth + 1)

    def _flatten(self, lookup_idx: int):
        out = []
        for st in self.lookups[lookup_idx].SubTable:
            ext = getattr(st, "extSubTable", None)
            out.append(ext if ext is not None else st)
        return out

    def lookup_type(self, lookup_idx: int) -> int:
        for st in self._flatten(lookup_idx):
            t = gsub_type_of(st)
            if t:
                return t
        return self.lookups[lookup_idx].LookupType

    # ---------- 规则归一化 ----------

    def direct_rules(self, lookup_idx: int):
        """直接替换规则：[(kind, st)] kind in single/multiple/ligature。"""
        out = []
        for st in self._flatten(lookup_idx):
            t = gsub_type_of(st)
            if t in (1, 2, 4):
                out.append(({1: "single", 2: "multiple", 4: "ligature"}[t], st))
        return out

    def context_rules(self, lookup_idx: int) -> List[dict]:
        """把 context/chained 全部归一化为统一结构。

        规则 dict：
          first_gids: set            首位 glyph 约束（coverage 或 class）
          input: [{"gids":set|None, "classes":set|None}]  长度=输入跨度
          backtrack: [gid-set]       顺序为紧邻位置向左
          lookahead: [gid-set]       顺序为紧邻位置向右
          records: [{seq:int, lookup:int}]
        """
        rules = []
        for st in self._flatten(lookup_idx):
            t = gsub_type_of(st)
            if t not in (5, 6):
                continue
            fmt = format_of(st)
            chained = t == 6
            if fmt == 1:
                cov = st.Coverage._gs_gid_set
                sets = st.ChainSubRuleSet if chained else st.SubRuleSet
                rule_attr = "ChainSubRule" if chained else "SubRule"
                for start_gid_set, rset in self._sets_with_anchor(sets, cov):
                    for rule in getattr(rset, rule_attr, None) or []:
                        input_constraints = [{"gids": start_gid_set, "classes": None}]
                        for gname in getattr(rule, "Input", []) or []:
                            gid = self.name_to_gid.get(gname)
                            input_constraints.append(
                                {"gids": {gid} if gid is not None else set(), "classes": None}
                            )
                        rules.append(
                            self._mk_rule(
                                input_constraints,
                                self._glyph_name_sets(getattr(rule, "Backtrack", [])),
                                self._glyph_name_sets(getattr(rule, "LookAhead", [])),
                                rule.SubstLookupRecord,
                            )
                        )
            elif fmt == 2:
                cdef = st.InputClassDef if chained else st.ClassDef
                cmap_classes = cdef._gs_class_map
                sets = st.ChainSubClassSet if chained else st.SubClassSet
                rule_attr = "ChainSubRule" if chained else "SubClassRule"
                for anchor_class, cset in enumerate(sets):
                    if cset is None:
                        continue
                    anchor_gids = {
                        gid for gid, cls in cmap_classes.items() if cls == anchor_class
                    }
                    for rule in getattr(cset, rule_attr, None) or []:
                        class_seq = list(getattr(rule, "Class", []))
                        input_constraints = [{"gids": anchor_gids, "classes": None}]
                        for cls in class_seq:
                            gids = {
                                gid
                                for gid, c in cmap_classes.items()
                                if c == cls
                            }
                            input_constraints.append({"gids": gids, "classes": None})
                        back = self._chained_class_sequence(
                            getattr(st, "BacktrackClassDef", None),
                            getattr(rule, "BacktrackClass", []),
                        )
                        look = self._chained_class_sequence(
                            getattr(st, "LookAheadClassDef", None),
                            getattr(rule, "LookAheadClass", []),
                        )
                        rules.append(
                            self._mk_rule(
                                input_constraints,
                                back,
                                look,
                                rule.SubstLookupRecord,
                            )
                        )
            else:  # format 3
                input_coverage = (
                    getattr(st, "InputCoverage", None)
                    if getattr(st, "InputCoverage", None) is not None
                    else st.Coverage
                )
                input_covs = [c._gs_gid_set for c in input_coverage]
                back_covs = [
                    c._gs_gid_set for c in getattr(st, "BacktrackCoverage", [])
                ]
                look_covs = [
                    c._gs_gid_set for c in getattr(st, "LookAheadCoverage", [])
                ]
                rules.append(
                    self._mk_rule(
                        [{"gids": s, "classes": None} for s in input_covs],
                        back_covs,
                        look_covs,
                        st.SubstLookupRecord,
                    )
                )
        return rules

    @staticmethod
    def _sets_with_anchor(sets, coverage):
        # fmt1 每个 SubRuleSet 对应 Coverage 中的一个 glyph，顺序对齐。
        anchors = sorted(coverage)
        out = []
        for idx, rset in enumerate(sets):
            if rset is not None and idx < len(anchors):
                out.append(({anchors[idx]}, rset))
        return out

    def _input_gids_from_coverage(self, _input, st):
        # fmt1 规则的 Input 是 glyph 名称列表（首位以外）。
        return None

    def _coverages_to_sets(self, glyph_names_or_covs):
        out = []
        for item in glyph_names_or_covs or []:
            if hasattr(item, "_gs_gid_set"):
                out.append(item._gs_gid_set)
            elif isinstance(item, str):
                gid = self.name_to_gid.get(item)
                out.append({gid} if gid is not None else set())
            else:
                out.append(set())
        return out

    def _glyph_name_sets(self, names):
        """SubRule/ChainSubRule 里的 glyph 名称序列 -> 逐位置 GID 集合。"""
        out = []
        for gname in names or []:
            gid = self.name_to_gid.get(gname)
            out.append({gid} if gid is not None else set())
        return out

    def _chained_class_sequence(self, classdef, class_ids):
        """链式 fmt2 的回溯/前瞻类序列 -> 逐位置 GID 集合。"""
        if classdef is None:
            return [set() for _ in class_ids or []]
        cmap_classes = classdef._gs_class_map
        out = []
        for cls in class_ids or []:
            out.append({gid for gid, c in cmap_classes.items() if c == cls})
        return out

    @staticmethod
    def _mk_rule(input_constraints, backtrack, lookahead, records):
        return {
            "input": input_constraints,
            "backtrack": backtrack,
            "lookahead": lookahead,
            "records": [
                {"seq": int(r.SequenceIndex), "lookup": int(r.LookupListIndex)}
                for r in records
            ],
        }

    def reverse_rules(self, lookup_idx: int):
        """类型 8：返回 (input_gid_sets, backtrack_sets, lookahead_sets, mapping)。"""
        out = []
        for st in self._flatten(lookup_idx):
            if gsub_type_of(st) != 8:
                continue
            inputs = [c._gs_gid_set for c in st.InputCoverage]
            back = [c._gs_gid_set for c in st.BacktrackCoverage]
            look = [c._gs_gid_set for c in st.LookAheadCoverage]
            mapping = {
                self.name_to_gid[k]: self.name_to_gid[v]
                for k, v in (st.GlyphAlternates if hasattr(st, "GlyphAlternates") else st.mapping).items()
                if k in self.name_to_gid and v in self.name_to_gid
            }
            out.append((inputs, back, look, mapping))
        return out

    # ---------- 特性顺序 ----------

    def feature_plan(self, enabled_tags) -> List[Tuple[str, int]]:
        if self.gsub is None or getattr(self.gsub, "table", None) is None:
            return []
        inner = self.gsub.table
        present = OrderedDict()
        if inner.FeatureList is not None:
            for fr in inner.FeatureList.FeatureRecord:
                present.setdefault(fr.FeatureTag, fr.Feature)
        tags = set(enabled_tags)
        ordered = [t for t in DEFAULT_FEATURE_ORDER if t in present and t in tags]
        for t in enabled_tags:
            if t in present and t in tags and t not in ordered:
                ordered.append(t)
        plan = []
        seen = set()
        for tag in ordered:
            for li in present[tag].LookupListIndex:
                if (tag, li) not in seen and li < len(self.lookups):
                    seen.add((tag, li))
                    plan.append((tag, li))
        return plan

    # ---------- 塑形 ----------

    def shape(self, nodes: List[dict], enabled_tags):
        buf = [dict(n, history=list(n.get("history", []))) for n in nodes]
        actions = []
        plan = self.feature_plan(enabled_tags)
        for _pass in range(SIM_PASSES):
            changed_pass = False
            for tag, li in plan:
                changed = True
                while changed and len(actions) < MAX_ACTIONS:
                    changed = self._apply_lookup(buf, tag, li, actions)
                    changed_pass = changed_pass or changed
            if not changed_pass:
                break
        return buf, actions

    def _apply_lookup(self, buf, tag, lookup_idx, actions) -> bool:
        ltype = self.lookup_type(lookup_idx)
        if ltype == 8:
            return self._apply_reverse(buf, tag, lookup_idx, actions)
        for pos in range(len(buf)):
            if buf[pos]["gid"] is None:
                continue
            # 直接替换。
            for kind, st in self.direct_rules(lookup_idx):
                hit = self._match_direct(buf, pos, kind, st)
                if hit is not None:
                    consumed, out_gids, detail = hit
                    if _is_expanding_noop(
                        kind, consumed, out_gids, buf, pos
                    ):
                        # f -> f + mark 这类“保留自身 + 追加”的多重替换只应
                        # 触发一次；跳过已带本 lookup 标记的节点，防止无限循环。
                        if _node_seen_lookup(buf[pos], tag, lookup_idx):
                            continue
                    self._replace(buf, pos, consumed, out_gids, tag, lookup_idx, detail, actions)
                    return True
            # 上下文替换。
            for rule in self.context_rules(lookup_idx):
                if self._match_context(buf, pos, rule):
                    if self._run_records(buf, pos, rule, tag, lookup_idx, actions):
                        return True
        return False


    def _match_direct(self, buf, pos, kind, st):
        gid = buf[pos]["gid"]
        if kind == "single":
            mapping = getattr(st, "mapping", None) or {}
            name = self.order[gid]
            out_name = mapping.get(name)
            if out_name is not None and out_name in self.name_to_gid:
                return 1, [self.name_to_gid[out_name]], {
                    "rule": "single",
                    "input": [name],
                    "output": [out_name],
                }
            return None
        if kind == "multiple":
            mappings = getattr(st, "mapping", None) or {}
            name = self.order[gid]
            out_names = mappings.get(name)
            if out_names and all(n in self.name_to_gid for n in out_names):
                return 1, [self.name_to_gid[n] for n in out_names], {
                    "rule": "multiple",
                    "input": [name],
                    "output": list(out_names),
                }
            return None
        # ligature
        ligs = getattr(st, "ligatures", None) or {}
        name = self.order[gid]
        candidates = ligs.get(name)
        if not candidates:
            return None
        # fontTools 已按组件数降序排列，这里再稳妥排一次。
        candidates = sorted(candidates, key=lambda l: -l.CompCount)
        for lig in candidates:
            comp_names = list(lig.Component)
            span = len(comp_names) + 1
            if pos + span > len(buf):
                continue
            ok = all(
                buf[pos + 1 + j]["gid"] is not None
                and self.order[buf[pos + 1 + j]["gid"]] == comp_names[j]
                for j in range(len(comp_names))
            )
            if ok:
                return span, [self.name_to_gid[lig.LigGlyph]], {
                    "rule": "ligature",
                    "input": [name] + comp_names,
                    "output": [lig.LigGlyph],
                }
        return None

    def _match_context(self, buf, pos, rule) -> bool:
        inputs = rule["input"]
        span = len(inputs)
        if pos + span > len(buf):
            return False
        for j, constraint in enumerate(inputs):
            gid = buf[pos + j]["gid"]
            if gid is None:
                return False
            allowed = constraint.get("gids")
            if allowed is not None and gid not in allowed:
                return False
        # backtrack：records 顺序 [紧邻-1, -2, ...]
        for k, allowed in enumerate(rule["backtrack"]):
            idx = pos - 1 - k
            if idx < 0:
                return False
            if isinstance(allowed, int):
                continue  # 类回溯在 _class_match 中处理（见链式 fmt2 增强）
            gid = buf[idx]["gid"]
            if gid is None or gid not in allowed:
                return False
        # lookahead：[紧邻 +span, +span+1, ...]
        for k, allowed in enumerate(rule["lookahead"]):
            idx = pos + span + k
            if idx >= len(buf):
                return False
            if isinstance(allowed, int):
                continue
            gid = buf[idx]["gid"]
            if gid is None or gid not in allowed:
                return False
        return True

    def _run_records(self, buf, pos, rule, tag, lookup_idx, actions) -> bool:
        ran = False
        # SequenceIndex 相对匹配起点；按位置排序保证确定性。
        for rec in sorted(rule["records"], key=lambda r: (r["seq"], r["lookup"])):
            target = pos + rec["seq"]
            if target >= len(buf) or buf[target]["gid"] is None:
                continue
            sub_actions_before = len(actions)
            if self._apply_lookup(buf, "%s>ctx" % tag, rec["lookup"], actions):
                ran = True
        return ran

    def _apply_reverse(self, buf, tag, lookup_idx, actions) -> bool:
        # reverse chaining：从右向左，单次扫描。
        for inputs, back, look, mapping in self.reverse_rules(lookup_idx):
            span = len(inputs)
            for pos in range(len(buf) - span, -1, -1):
                if all(
                    buf[pos + j]["gid"] in inputs[j] for j in range(span)
                ) and all(
                    pos - 1 - k >= 0 and buf[pos - 1 - k]["gid"] in back[k]
                    for k in range(len(back))
                ) and all(
                    pos + span + k < len(buf)
                    and buf[pos + span + k]["gid"] in look[k]
                    for k in range(len(look))
                ):
                    gid = buf[pos]["gid"]
                    out = mapping.get(gid)
                    if out is not None and out != gid:
                        self._replace(
                            buf, pos, span, [out] if span == 1 else [
                                mapping.get(buf[pos + j]["gid"], buf[pos + j]["gid"])
                                for j in range(span)
                            ],
                            tag, lookup_idx,
                            {"rule": "reverse", "input": [self.order[gid]],
                             "output": [self.order[out]]},
                            actions,
                        )
                        return True
        return False

    def _replace(self, buf, pos, consumed, out_gids, tag, lookup_idx, detail, actions):
        consumed_nodes = buf[pos : pos + consumed]
        cps = []
        for node in consumed_nodes:
            cps.extend(node.get("cps", []))
        new_nodes = []
        for k, gid in enumerate(out_gids):
            new_nodes.append(
                {
                    "gid": gid,
                    "cps": list(cps) if k == 0 else [],
                    "kind": "subst",
                    "history": [],
                }
            )
        buf[pos : pos + consumed] = new_nodes
        action = {
            "feature": tag,
            "lookup_index": lookup_idx,
            "lookup_type": GSUB_TYPE_NAMES.get(
                self.lookups[lookup_idx].LookupType, str(self.lookups[lookup_idx].LookupType)
            ),
            "input_gids": [n["gid"] for n in consumed_nodes],
            "input_names": [
                self.order[n["gid"]] if n["gid"] is not None else None
                for n in consumed_nodes
            ],
            "output_gids": list(out_gids),
            "output_names": [self.order[g] for g in out_gids],
            "cps": cps,
            "detail": detail,
        }
        for node in new_nodes:
            node["history"].append(action)
        actions.append(action)

    # ---------- 静态可达边（特性闭合） ----------

    def lookup_edges(self, lookup_idx: int) -> List[Tuple[int, int, dict]]:
        """返回 lookup 的静态有向边 (input_gid -> output_gid, meta)。

        用于 reachable 闭合：保留某个特性时，从样本 glyph 出发能走到的
        替代目标全部纳入，即使样本文本没有直接触发。
        """
        edges = []
        ltype = self.lookup_type(lookup_idx)
        for kind, st in self.direct_rules(lookup_idx):
            if kind == "single":
                for src_name, dst_name in (st.mapping or {}).items():
                    s, d = self.name_to_gid.get(src_name), self.name_to_gid.get(dst_name)
                    if s is not None and d is not None:
                        edges.append((s, d, {"rule": "single"}))
            elif kind == "multiple":
                for src_name, dst_names in (st.mapping or {}).items():
                    s = self.name_to_gid.get(src_name)
                    if s is None:
                        continue
                    for dst_name in dst_names:
                        d = self.name_to_gid.get(dst_name)
                        if d is not None:
                            edges.append((s, d, {"rule": "multiple"}))
            else:
                for first_name, ligs in (st.ligatures or {}).items():
                    first = self.name_to_gid.get(first_name)
                    for lig in ligs:
                        d = self.name_to_gid.get(lig.LigGlyph)
                        if first is not None and d is not None:
                            comp_gids = [
                                self.name_to_gid[n]
                                for n in lig.Component
                                if n in self.name_to_gid
                            ]
                            edges.append(
                                (first, d, {"rule": "ligature", "components": comp_gids})
                            )
        # 上下文：把内部 lookup 的边也暴露出来（保守地认为首位 coverage
        # 内 glyph 可触发内部替代）。
        for rule in self.context_rules(lookup_idx):
            anchors = rule["input"][0]["gids"] if rule["input"] else set()
            for rec in rule["records"]:
                if rec["lookup"] >= len(self.lookups):
                    continue
                for src, dst, meta in self.lookup_edges(rec["lookup"]):
                    if src in anchors or not anchors:
                        edges.append((src, dst, dict(meta, contextual=True)))
        if ltype == 8:
            for inputs, back, look, mapping in self.reverse_rules(lookup_idx):
                for src, dst in mapping.items():
                    edges.append((src, dst, {"rule": "reverse"}))
        return edges

    # ---------- 特性摘要 ----------

    def summarize(self) -> dict:
        return {
            "gsub": self._summarize_table(self.gsub),
            "gpos": self._summarize_table(self.gpos),
        }

    def _summarize_table(self, table) -> dict:
        if table is None or getattr(table, "table", None) is None:
            return {"present": False}
        inner = table.table
        lookups = []
        list_obj = getattr(inner, "LookupList", None)
        if list_obj is not None:
            for idx, lu in enumerate(list_obj.Lookup):
                sub_names = []
                for st in lu.SubTable:
                    ext = getattr(st, "extSubTable", st)
                    sub_names.append(
                        "%s fmt%s" % (GSUB_TYPE_NAMES.get(gsub_type_of(ext), "?"), format_of(ext) or "?")
                    )
                lookups.append(
                    {
                        "index": idx,
                        "type": lu.LookupType,
                        "type_name": GSUB_TYPE_NAMES.get(lu.LookupType, "unknown"),
                        "subtables": sub_names,
                    }
                )
        features = []
        if getattr(inner, "FeatureList", None) is not None:
            for fr in inner.FeatureList.FeatureRecord:
                features.append(
                    {"tag": fr.FeatureTag, "lookups": list(fr.Feature.LookupListIndex)}
                )
        scripts = []
        if getattr(inner, "ScriptList", None) is not None:
            for sr in inner.ScriptList.ScriptRecord:
                lang_sys = [("default", sr.Script.DefaultLangSys)]
                for lr in sr.Script.LangSysRecord:
                    lang_sys.append((lr.LangSysTag, lr.LangSys))
                lang_out = []
                for name, ls in lang_sys:
                    if ls is None:
                        continue
                    lang_out.append(
                        {
                            "language": name,
                            "required_feature": ls.ReqFeatureIndex,
                            "features": list(ls.FeatureIndex),
                        }
                    )
                scripts.append({"script": sr.ScriptTag, "langsys": lang_out})
        return {
            "present": True,
            "lookups": lookups,
            "features": features,
            "scripts": scripts,
        }
