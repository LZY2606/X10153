"""离线本地预览：glyph 轮廓 SVG 与字符 -> glyph 关系图。

不追求与系统渲染像素一致，目标是清楚呈现：
  - 每个样本簇实际用到哪些 glyph；
  - 哪些字符缺失（红框）或落到 .notdef（虚线框）；
  - 哪些 glyph 来自连字/替代（蓝框 + 特性标注）；
  - 复合 glyph 与组件的依赖边、闭合策略纳入的 glyph。
"""

from typing import Dict

from fontTools.pens.svgPathPen import SVGPathPen

from .segments import cp_label

CELL = 96
PAD = 12
GRAPH_COL_W = 250
GRAPH_ROW_H = 58
GRAPH_NODE_H = 40
GRAPH_NODE_W = GRAPH_COL_W - 36
GRAPH_TOP = 44


def glyph_svg_path(font, glyph_name: str) -> str:
    glyph_set = font.getGlyphSet()
    pen = SVGPathPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    return pen.getCommands()


def render_sample_svg(analysis, sample_record: dict) -> str:
    font = analysis.font
    upm = font["head"].unitsPerEm
    scale = (CELL - 2 * PAD) / upm
    cells = []
    x = 0
    for cluster in sample_record["clusters"]:
        for j, gid in enumerate(cluster["output_gids"]):
            has_missing = any(i["status"] == "missing" for i in cluster["input"])
            has_notdef = any(i["status"] == "notdef" for i in cluster["input"])
            if gid is None:
                cells.append(_missing_cell(x, cluster))
            elif gid == 0 and has_notdef:
                cells.append(_notdef_cell(x))
            else:
                cells.append(
                    _glyph_cell(x, font, gid, analysis.glyph_order[gid], scale, cluster)
                )
            x += CELL
    width = max(x, CELL)
    height = CELL + 34
    return (
        '<svg class="preview-svg" viewBox="0 0 %d %d" width="%d" height="%d" '
        'xmlns="http://www.w3.org/2000/svg">%s</svg>'
        % (width, height, width, height, "".join(cells))
    )


def _glyph_cell(x, font, gid, name, scale, cluster):
    d = glyph_svg_path(font, name)
    upm = font["head"].unitsPerEm
    actions = cluster.get("actions", [])
    replaced = any(gid in a["output_gids"] for a in actions)
    stroke = "#2563eb" if replaced else "#374151"
    parts = []
    if replaced:
        parts.append(
            '<rect x="%d" y="16" width="%d" height="%d" rx="6" class="cell-box alt"/>'
            % (x + 2, CELL - 4, CELL - 30)
        )
        feats = ",".join(
            sorted(
                {
                    a["feature"].split(">")[0]
                    for a in actions
                    if gid in a["output_gids"]
                }
            )
        )
        parts.append(
            '<text x="%d" y="11" class="cell-badge" text-anchor="middle">%s</text>'
            % (x + CELL / 2, _esc(feats))
        )
    parts.append(
        '<g transform="translate(%g,%g) scale(%g,-%g) translate(0,-%d)">'
        '<path d="%s" fill="%s"/></g>'
        % (x + PAD, PAD + upm * scale + 2, scale, scale, upm, d, stroke)
    )
    parts.append(
        '<text x="%d" y="%d" class="cell-label" text-anchor="middle">%s</text>'
        % (x + CELL / 2, CELL + 6, _esc("#%d %s" % (gid, name)))
    )
    return "".join(parts)


def _missing_cell(x, cluster):
    return (
        '<rect x="%d" y="18" width="%d" height="%d" rx="6" class="cell-box missing"/>'
        '<text x="%d" y="%d" class="missing-x" text-anchor="middle">缺</text>'
        '<text x="%d" y="%d" class="cell-label" text-anchor="middle">%s</text>'
        % (
            x + 2, CELL - 4, CELL - 30,
            x + CELL / 2, CELL / 2 + 8,
            x + CELL / 2, CELL + 6, _esc(cp_label(cluster["base_cp"])),
        )
    )


def _notdef_cell(x):
    return (
        '<rect x="%d" y="18" width="%d" height="%d" rx="6" class="cell-box notdef-box"/>'
        '<text x="%d" y="%d" class="missing-x" text-anchor="middle">.notdef</text>'
        '<text x="%d" y="%d" class="cell-label" text-anchor="middle">GID 0</text>'
        % (x + 2, CELL - 4, CELL - 30, x + CELL / 2, CELL / 2 + 8,
           x + CELL / 2, CELL + 6)
    )


def relation_graph(plan: dict) -> dict:
    nodes: Dict[str, dict] = {}
    edges = []
    edge_keys = set()

    def add_node(node_id, label, kind):
        nodes.setdefault(node_id, {"id": node_id, "label": label, "kind": kind})

    def add_edge(src, dst, label):
        key = (src, dst, label)
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append({"from": src, "to": dst, "label": label})

    for glyph in plan["glyphs"]:
        gid = glyph["gid"]
        gnode = "g%d" % gid
        add_node(gnode, "#%d %s" % (gid, glyph["name"]), "glyph")
        for origin in glyph["origins"]:
            kind = origin["kind"]
            if kind in ("character", "variation", "combining", "mapped_notdef"):
                cid = "c%x" % origin["cp"]
                label = cp_label(origin["cp"])
                if kind == "variation":
                    label += " " + origin.get("variation_selector", "")
                add_node(cid, label, "character")
                add_edge(cid, gnode, _origin_edge_label(kind, origin))
            elif kind == "layout":
                for cp in origin.get("cps", []):
                    cid = "c%x" % cp
                    add_node(cid, cp_label(cp), "character")
                    add_edge(
                        cid,
                        gnode,
                        "layout:%s/%s"
                        % (origin.get("feature", ""), origin.get("rule", "")),
                    )
            elif kind == "component":
                parent = "g%d" % origin["parent_gid"]
                add_node(parent, parent, "glyph")
                add_edge(gnode, parent, "component-of")
            elif kind == "closure":
                add_edge(
                    "policy",
                    gnode,
                    "closure:%s/%s"
                    % (origin.get("policy"), origin.get("feature") or ""),
                )
        for dep in glyph.get("components", []):
            add_edge("g%d" % dep, gnode, "component")
    add_node("policy", "闭合策略", "policy")

    layers = _layer_nodes(nodes, edges)
    positions = _layout_positions(nodes, layers)
    layer_counts: Dict[int, int] = {}
    for layer in layers.values():
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
    max_layer = max(layers.values()) if layers else 0
    node_out = []
    for nid, node in nodes.items():
        x, y = positions[nid]
        node_out.append(
            dict(
                node,
                layer=layers[nid],
                x=x,
                y=y,
                w=GRAPH_NODE_W,
                h=GRAPH_NODE_H,
            )
        )
    node_out.sort(key=lambda n: (n["layer"], n["y"], n["id"]))
    return {
        "nodes": node_out,
        "edges": edges,
        "width": (max_layer + 1) * GRAPH_COL_W,
        "height": (max(layer_counts.values()) if layer_counts else 1) * GRAPH_ROW_H
        + GRAPH_TOP + 20,
    }


def _origin_edge_label(kind, origin):
    return {
        "character": "cmap",
        "combining": "mark",
        "variation": "vs",
        "mapped_notdef": "cmap->.notdef",
    }.get(kind, kind)


def _layer_nodes(nodes, edges):
    layers = {
        nid: (0 if nodes[nid]["kind"] == "character" else 1) for nid in nodes
    }
    if "policy" in layers:
        layers["policy"] = 0
    for _ in range(len(nodes) + 2):
        changed = False
        for edge in edges:
            src, dst = edge["from"], edge["to"]
            if nodes[dst]["kind"] == "character":
                continue
            base = 0 if dst == "policy" else layers.get(src, 0)
            candidate = base + 1 if dst != "policy" else 0
            if layers.get(dst, 0) < candidate:
                layers[dst] = candidate
                changed = True
        if not changed:
            break
    return layers


def _layout_positions(nodes, layers):
    buckets: Dict[int, List[str]] = {}
    for nid in nodes:
        buckets.setdefault(layers[nid], []).append(nid)

    def sort_key(nid):
        kind = nodes[nid]["kind"]
        if kind == "character":
            return (1, int(nid[1:], 16))
        if kind == "policy":
            return (2, 0)
        return (0, int(nid[1:]))

    positions = {}
    for layer, ids in buckets.items():
        ids.sort(key=sort_key)
        for row, nid in enumerate(ids):
            positions[nid] = (
                layer * GRAPH_COL_W + 18,
                GRAPH_TOP + row * GRAPH_ROW_H,
            )
    return positions


def _esc(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
