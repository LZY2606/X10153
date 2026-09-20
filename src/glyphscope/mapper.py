"""cmap 读取与“字符 -> glyph”解析。

两类问题严格区分：

- ``missing``：cmap 中根本没有该码点（或码点+变体选择符）的映射；
- ``notdef``：存在映射，但映射结果是 GID 0（.notdef），
  或变体默认回退到的基字符映射本身就是 .notdef。

format-14 子表三种情况：
1. 非默认 UVS，显式指定变体 glyph；
2. 默认 UVS（glyph=None），回退基字符 cmap；
3. UVS 未登记，按无变体处理并标注 ``variant_uncovered``。
"""

from typing import Dict, List, Optional, Tuple

from .segments import Cluster


class CmapIndex:
    """对字体 cmap 建立的只读索引（GID 口径）。"""

    def __init__(self, font, name_to_gid: Dict[str, int]):
        self.font = font
        self.name_to_gid = name_to_gid
        self.unicode_subtables: List[str] = []
        self.base_map: Dict[int, Tuple[int, List[str]]] = {}
        # vs -> { cp -> (gid|None, source) }
        self.variant_map: Dict[int, Dict[int, Tuple[Optional[int], str]]] = {}
        self._build()

    @staticmethod
    def _source(sub) -> str:
        return "cmap fmt%d (%d/%d)" % (
            sub.format,
            sub.platformID,
            sub.platEncID,
        )

    def _build(self):
        cmap_table = self.font["cmap"]
        for sub in cmap_table.tables:
            if sub.format == 14:
                self._index_format14(sub)
                continue
            if sub.platformID not in (0, 3):
                continue
            source = self._source(sub)
            table_cmap = getattr(sub, "cmap", None) or {}
            for cp, gname in table_cmap.items():
                gid = self.name_to_gid.get(gname)
                if gid is None:
                    continue
                existing = self.base_map.get(cp)
                if existing is None:
                    self.base_map[cp] = (gid, [source])
                elif source not in existing[1]:
                    existing[1].append(source)
            self.unicode_subtables.append(source)
        # 用 best cmap 兜底（覆盖平台 ID 特殊但可用的情况）。
        for cp, gname in (self.font.getBestCmap() or {}).items():
            gid = self.name_to_gid.get(gname)
            if gid is not None and cp not in self.base_map:
                self.base_map[cp] = (gid, ["cmap best"])

    def _index_format14(self, sub):
        uvs_dict = getattr(sub, "uvsDict", None)
        if not uvs_dict:
            return
        source = self._source(sub)
        for vs, entries in uvs_dict.items():
            bucket = self.variant_map.setdefault(vs, {})
            for cp, gname in entries:
                bucket[cp] = (
                    None if gname is None else self.name_to_gid.get(gname),
                    source,
                )

    def lookup(self, cp: int, vs: Optional[int] = None) -> dict:
        """查单个码点（可带变体选择符）。

        返回字段：
          status: mapped / missing / notdef
          gid: 映射 GID（missing 时为 None）
          sources: 命中的 cmap 子表标签
          variant_registered / variant_default_uvs / variant_uncovered
        """
        result = {
            "cp": cp,
            "vs": vs,
            "status": "missing",
            "gid": None,
            "sources": [],
            "variant_registered": False,
            "variant_default_uvs": False,
            "variant_uncovered": False,
        }
        base = self.base_map.get(cp)
        base_gid = base[0] if base else None
        sources = list(base[1]) if base else []
        if vs is not None:
            bucket = self.variant_map.get(vs)
            entry = bucket.get(cp) if bucket is not None else None
            if entry is not None:
                variant_gid, source = entry
                result["variant_registered"] = True
                sources.append(source)
                if variant_gid is None:
                    result["variant_default_uvs"] = True
                    gid = base_gid  # 默认 UVS -> 基字形
                else:
                    gid = variant_gid
            else:
                result["variant_uncovered"] = True
                gid = base_gid
        else:
            gid = base_gid

        if gid is None:
            result["status"] = "missing"
        elif gid == 0:
            result["status"] = "notdef"
        else:
            result["status"] = "mapped"
            result["gid"] = gid
        result["sources"] = sorted(set(sources))
        return result

    def initial_sequence(self, cluster: Cluster) -> List[dict]:
        """把一个簇转成布局引擎输入序列。

        每个元素：``{"cp", "kind": base|mark|joiner|control, "vs", "lookup"}``。
        基字符携带变体选择符；组合标记与 ZWJ/ZWNJ 各自查普通 cmap。
        """
        seq = []
        base_item = {
            "cp": cluster.base,
            "kind": "base",
            "vs": cluster.variation_selector,
            "lookup": self.lookup(cluster.base, cluster.variation_selector),
        }
        seq.append(base_item)
        for cp in cluster.marks:
            seq.append(
                {"cp": cp, "kind": "mark", "vs": None, "lookup": self.lookup(cp)}
            )
        # ZWJ/ZWNJ 各自成簇（segments 中即如此），此处不处理。
        return seq
