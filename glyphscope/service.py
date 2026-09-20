"""应用编排：上传、隔离、建计划、导出、对比、SVG 预览数据。"""
from __future__ import annotations


from .errors import FontCorrupt, UnsupportedFont
from .font import LoadedFont
from .planner import (PlanOptions, SampleRun, make_plan,
                      POLICY_CLOSURE)
from .storage import Storage
from .subset import export_subset


class Service:
    def __init__(self, storage=None):
        self.storage = storage or Storage()
        self._font_cache = {}

    # ---- 上传 ----
    def upload(self, raw, filename=None):
        digest, path = self.storage.save_raw_font(raw)
        try:
            font = LoadedFont(raw)
        except FontCorrupt as exc:
            record = self.storage.quarantine(
                raw, exc.reason, exc.table, exc.detail)
            return {
                "status": "quarantined",
                "digest": digest,
                "quarantine": record,
            }
        except UnsupportedFont as exc:
            record = self.storage.quarantine(
                raw, "unsupported:" + str(exc), table=None,
                detail="scaler 不支持")
            return {
                "status": "unsupported",
                "digest": digest,
                "quarantine": record,
            }
        self._font_cache[digest] = font
        meta = self._meta_json(font)
        self.storage.save_font_meta(digest, meta)
        return {
            "status": "ok",
            "digest": digest,
            "filename": filename,
            "meta": meta,
        }

    def get_font(self, digest):
        if digest in self._font_cache:
            return self._font_cache[digest]
        raw = self.storage.load_raw_font(digest)
        font = LoadedFont(raw)
        self._font_cache[digest] = font
        return font

    def _meta_json(self, font):
        m = font.meta
        return {
            "family": m.family,
            "sfnt_version": m.sfnt_version,
            "scaler_type": m.scaler_type,
            "num_glyphs": m.num_glyphs,
            "units_per_em": m.units_per_em,
            "table_digest": m.table_digest,
            "tables": m.table_summaries,
            "names": m.names,
            "license_text": m.license_text,
            "license_url": m.license_url,
            "features": m.features,
            "gpos_features": m.gpos_features,
            "warnings": font.warnings,
        }

    # ---- 计划 ----
    def create_plan(self, digest, samples, feature_policy=POLICY_CLOSURE,
                    features=None, include_notdef=False,
                    apply_alternates=False, compose_combining=True,
                    plan_only=False):
        font = self.get_font(digest)
        runs = [SampleRun(text=s["text"], label=s.get("label", ""))
                for s in samples if s.get("text")]
        if features is None:
            from .planner import DEFAULT_FEATURES
            features = list(DEFAULT_FEATURES)
        options = PlanOptions(
            feature_policy=feature_policy,
            features=list(features),
            include_notdef=bool(include_notdef),
            apply_alternates=bool(apply_alternates),
            compose_combining=bool(compose_combining))
        plan = make_plan(font, runs, options)
        version, path = self.storage.save_plan(digest, plan)
        result = {
            "status": "ok",
            "version": version,
            "content_hash": plan["content_hash"],
            "plan": plan,
        }
        if not plan_only:
            data, gmap, notes = export_subset(font, plan)
            export_path = self.storage.save_export(
                digest, plan["content_hash"], version, data)
            result["export"] = {
                "path": export_path,
                "size": len(data),
                "glyph_map": {str(k): v for k, v in gmap.items()},
                "notes": notes,
            }
        else:
            result["export"] = None
        return result

    def get_plan(self, digest, version):
        return self.storage.load_plan(digest, int(version))

    def list_plans(self, digest):
        return self.storage.list_plans(digest)

    def export_plan(self, digest, version):
        plan = self.storage.load_plan(digest, version)
        font = self.get_font(digest)
        data, gmap, notes = export_subset(font, plan)
        path = self.storage.save_export(
            digest, plan["content_hash"], int(version), data)
        return data, path, gmap, notes

    # ---- 字符->glyph 关系图 ----
    def graph(self, plan):
        nodes = {}
        links = []

        def add_node(node_id, kind, label, **extra):
            if node_id not in nodes:
                node = {"id": node_id, "kind": kind, "label": label}
                node.update(extra)
                nodes[node_id] = node

        for edge in plan.get("edges", []):
            if edge["source_type"] == "char":
                cp = edge["source_cp"]
                vs = edge.get("variation_selector")
                src = "char:%x:%s" % (cp, "%x" % vs if vs else "")
                label = _pretty_char(cp, vs)
                add_node(src, "char", label, cp=cp,
                         variation_selector=vs)
            else:
                src = "glyph:%d" % edge["source_gid"]
                add_node(src, "glyph", _glyph_label(plan, edge["source_gid"]),
                         glyph_index=edge["source_gid"])
            dst = "glyph:%d" % edge["target_gid"]
            add_node(dst, "glyph", _glyph_label(plan, edge["target_gid"]),
                     glyph_index=edge["target_gid"])
            links.append({
                "source": src,
                "target": dst,
                "kind": edge["kind"],
                "feature": edge.get("feature"),
                "lookup": edge.get("lookup"),
            })

        # 缺失字符节点
        for miss in plan.get("missing", []):
            for cp in miss["codepoints"]:
                node_id = "missing:%x" % cp
                add_node(node_id, "missing", _cp_label(cp), cp=cp)
        return {"nodes": list(nodes.values()), "links": links}

    # ---- 本地轮廓预览 ----
    def glyph_svg(self, digest, gid, size=120):
        font = self.get_font(digest)
        gid = int(gid)
        info = font.glyphs[gid]
        upem = font.head["units_per_em"]
        if info.is_composite:
            path = _composite_path(font, gid)
            x0, y0, x1, y1 = info.x_min, info.y_min, info.x_max, info.y_max
        elif info.number_of_contours > 0:
            outline = _simple_outline(font, gid)
            if outline is None:
                return _empty_svg(size, info.name)
            end_pts, on_curve, xs, ys = outline
            path = _outline_to_path(end_pts, on_curve, xs, ys)
            x0, y0, x1, y1 = info.x_min, info.y_min, info.x_max, info.y_max
        else:
            return _empty_svg(size, info.name)
        return _wrap_svg(path, x0, y0, x1, y1, upem, size, info.name,
                         info.advance)

    # ---- 计划对比 ----
    def compare(self, plan_a, plan_b):
        ga = {g["glyph_index"]: g for g in plan_a["glyphs"]}
        gb = {g["glyph_index"]: g for g in plan_b["glyphs"]}
        only_a = sorted(set(ga) - set(gb))
        only_b = sorted(set(gb) - set(ga))
        common = sorted(set(ga) & set(gb))
        changed = []
        for gid in common:
            oa = sorted((o["kind"], o.get("feature")) for o in ga[gid]["origins"])
            ob = sorted((o["kind"], o.get("feature")) for o in gb[gid]["origins"])
            if oa != ob:
                changed.append({
                    "glyph_index": gid,
                    "name": ga[gid]["name"],
                    "a_origins": ga[gid]["origins"],
                    "b_origins": gb[gid]["name"] and gb[gid]["origins"],
                })
        return {
            "a": {"version": plan_a.get("version"),
                  "content_hash": plan_a.get("content_hash"),
                  "stats": plan_a.get("stats"),
                  "strategy": plan_a.get("strategy"),
                  "samples": plan_a.get("samples")},
            "b": {"version": plan_b.get("version"),
                  "content_hash": plan_b.get("content_hash"),
                  "stats": plan_b.get("stats"),
                  "strategy": plan_b.get("strategy"),
                  "samples": plan_b.get("samples")},
            "only_in_a": [_glyph_brief(ga[g]) for g in only_a],
            "only_in_b": [_glyph_brief(gb[g]) for g in only_b],
            "changed_origins": changed,
            "common_count": len(common),
        }


def _glyph_label(plan, gid):
    for g in plan["glyphs"]:
        if g["glyph_index"] == gid:
            return "%d %s" % (gid, g["name"])
    return "gid%d" % gid


def _glyph_brief(g):
    return {"glyph_index": g["glyph_index"], "name": g["name"],
            "included_by": g["included_by"],
            "origins_kinds": [o["kind"] for o in g["origins"]]}


def _cp_label(cp):
    if cp > 0xFFFF:
        return "U+%05X" % cp
    return "U+%04X" % cp


def _pretty_char(cp, vs=None):
    text = chr(cp)
    if vs:
        text += chr(vs)
        return "%s %s+%s" % (text, _cp_label(cp), _cp_label(vs))
    return "%s %s" % (text, _cp_label(cp))


def _simple_outline(font, gid):
    from .glyf import parse_glyf_outline
    off = font.loca_offsets[gid]
    end = font.loca_offsets[gid + 1]
    if off == end:
        return None
    return parse_glyf_outline(font.glyf_data, off, end)


def _outline_to_path(end_pts, on_curve, xs, ys):
    contours = []
    start = 0
    for ep in end_pts:
        pts = list(zip(on_curve[start:ep + 1], xs[start:ep + 1],
                       ys[start:ep + 1]))
        contours.append(pts)
        start = ep + 1
    parts = []
    for pts in contours:
        # 简化处理：夹具/测试字形全部 on-curve；若有离曲线点用二次贝塞尔
        parts.append(_single_contour_path(pts))
    return " ".join(parts)


def _single_contour_path(pts):
    if not pts:
        return ""
    # 若首点离曲线，TrueType 约定最后一个点为隐式起点
    on = [p for p in pts if p[0]]
    if not on:
        return ""
    if not pts[0][0]:
        pts = pts + pts[:1]
    x0, y0 = pts[0][1], pts[0][2]
    cmds = ["M%d,%d" % (x0, y0)]
    i = 1
    n = len(pts)
    while i < n:
        on_curve, x, y = pts[i]
        if on_curve:
            cmds.append("L%d,%d" % (x, y))
            i += 1
        else:
            # off-curve：若下一点也 off-curve，插入中点
            if i + 1 < n and not pts[i + 1][0]:
                mx = (x + pts[i + 1][1]) / 2
                my = (y + pts[i + 1][2]) / 2
                cmds.append("Q%d,%d %d,%d" % (x, y, mx, my))
                i += 1
            elif i + 1 < n:
                cmds.append("Q%d,%d %d,%d" % (x, y, pts[i + 1][1],
                                              pts[i + 1][2]))
                i += 2
            else:
                i += 1
    cmds.append("Z")
    return " ".join(cmds)


def _composite_path(font, gid):
    from .glyf import flatten_components
    parts = []
    for child_gid, matrix in flatten_components(font.glyphs, gid):
        outline = _simple_outline(font, child_gid)
        if outline is None:
            continue
        end_pts, on_curve, xs, ys = outline
        xx, yx, xy, yy, dx, dy = matrix
        txs = [xx * x + xy * y + dx for x, y in zip(xs, ys)]
        tys = [yx * x + yy * y + dy for x, y in zip(xs, ys)]
        parts.append(_outline_to_path(end_pts, on_curve, txs, tys))
    return " ".join(parts)


def _wrap_svg(path, x0, y0, x1, y1, upem, size, name, advance):
    width = max(x1 - x0, 1)
    height = max(y1 - y0, 1)
    padding = size * 0.12
    scale = (size - 2 * padding) / max(width, height)
    vb_w = advance * scale if advance else width * scale
    vb_h = size
    # 转换：字形 y 翻转
    translate_x = padding - x0 * scale
    translate_y = padding - y0 * scale + height * scale
    transform = ("translate(%.2f,%.2f) scale(%.4f,-%.4f)"
                 % (translate_x, translate_y, scale, scale))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.1f %d" '
        'width="%.1f" height="%d">'
        '<rect width="100%%" height="100%%" fill="#fff"/>'
        '<line x1="0" y1="%.1f" x2="%.1f" y2="%.1f" '
        'stroke="#e5e7eb" stroke-width="1"/>'
        '<path d="%s" transform="%s" fill="#111827"/>'
        '<text x="4" y="14" font-family="sans-serif" font-size="10" '
        'fill="#6b7280">%s</text>'
        '</svg>'
    ) % (vb_w, size, vb_w, size,
         size - padding + 2, vb_w, size - padding + 2,
         path, transform, name)


def _empty_svg(size, name):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'width="%d" height="%d">'
        '<rect width="100%%" height="100%%" fill="#f9fafb"/>'
        '<text x="50%%" y="50%%" text-anchor="middle" dy=".3em" '
        'font-family="sans-serif" font-size="11" fill="#9ca3af">%s</text>'
        '</svg>'
    ) % (size, size, size, size, name or "(empty)")
