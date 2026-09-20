"""统一字体加载：保存原始字节、读取所有关键表并全量校验。

任何结构损坏都会抛 FontCorrupt，由上层把整份字体放入隔离区。
"""
from __future__ import annotations

import hashlib

from . import cmap as cmap_mod
from . import glyf as glyf_mod
from . import layout
from . import nametbl
from . import post as post_mod
from .errors import FontCorrupt
from .models import FontMeta, glyph_name_for
from .sfnt import parse_sfnt, table_checksum


class LoadedFont:
    def __init__(self, raw):
        self.raw = bytes(raw)
        self.digest = hashlib.sha256(self.raw).hexdigest()
        sfnt = parse_sfnt(self.raw)
        self.sfnt = sfnt
        self.warnings = []

        head = glyf_mod.parse_head(sfnt.require("head"))
        self.head = head
        maxp = glyf_mod.parse_maxp(sfnt.require("maxp"))
        self.maxp = maxp
        num_glyphs = maxp["num_glyphs"]
        if num_glyphs == 0:
            raise FontCorrupt("maxp.numGlyphs 为 0", "maxp")
        self.num_glyphs = num_glyphs

        hhea = glyf_mod.parse_hhea(sfnt.require("hhea"))
        self.hhea = hhea
        self.metrics = glyf_mod.parse_hmtx(
            sfnt.require("hmtx"), num_glyphs,
            hhea["number_of_h_metrics"])

        loca_r = sfnt.require("loca")
        loca_offsets = glyf_mod.parse_loca(
            loca_r, num_glyphs, head["index_to_loc_format"])
        glyf_rec = sfnt.require("glyf")
        self.glyf_data = glyf_rec.data
        self.loca_offsets = loca_offsets
        self.glyphs = glyf_mod.parse_glyf(
            glyf_rec.data, loca_offsets, num_glyphs)

        cmap = cmap_mod.parse_cmap(sfnt.require("cmap"))
        self.cmap = cmap["mapping"]
        self.cmap_subtables = cmap["subtables"]
        self.var_selectors = cmap["variations"]

        post_names = None
        if sfnt.has("post"):
            post = post_mod.parse_post(sfnt.reader("post"), num_glyphs)
            post_names = post["names"]
            self.post_version = post["version"]
        for info in self.glyphs:
            info.name = glyph_name_for(info.index, post_names, num_glyphs)
            if info.index < len(self.metrics):
                info.advance = self.metrics[info.index][0]

        name_data = {"records": [], "names": {}, "warnings": []}
        if sfnt.has("name"):
            name_data = nametbl.parse_name(sfnt.reader("name"))
            self.warnings.extend(name_data["warnings"])
        self.name_records = name_data["records"]
        self.names = name_data["names"]

        self.gsub = None
        if sfnt.has("GSUB"):
            self.gsub = layout.parse_gsub(sfnt.reader("GSUB"))
        self.gpos_features = []
        if sfnt.has("GPOS"):
            self.gpos_features = layout.parse_feature_tags_only(
                sfnt.reader("GPOS"))

        self._validate_cmap_gids()
        self.meta = self._build_meta()

    def _validate_cmap_gids(self):
        for cp, gid in self.cmap.items():
            if gid >= self.num_glyphs:
                raise FontCorrupt("cmap glyphId 越界", "cmap",
                                  "cp=U+%04X gid=%d num=%d" %
                                  (cp, gid, self.num_glyphs))
        for var in self.var_selectors.values():
            if var.non_default_mappings:
                for cp, gid in var.non_default_mappings:
                    if gid >= self.num_glyphs:
                        raise FontCorrupt(
                            "cmap format14 glyphId 越界", "cmap",
                            "UVS=%04X cp=%04X gid=%d" %
                            (var.unicode_value_selectors, cp, gid))
        if self.gsub:
            for lk in self.gsub["lookups"]:
                self._validate_lookup_gids(lk)

    def _validate_lookup_gids(self, lk):
        for st in lk.subtables:
            for gid in st.coverage:
                if gid >= self.num_glyphs:
                    raise FontCorrupt("GSUB coverage gid 越界", "GSUB")
            rule = st.rule
            if rule.kind == "single":
                targets = rule.mapping.values()
            elif rule.kind in ("multiple", "alternate"):
                targets = (g for seq in rule.mapping.values() for g in seq)
            elif rule.kind == "ligature":
                targets = (e[1] for entries in rule.ligsets.values()
                           for e in entries)
            else:
                targets = []
            for gid in targets:
                if gid >= self.num_glyphs:
                    raise FontCorrupt("GSUB 替换目标 gid 越界", "GSUB")

    def _build_meta(self):
        summaries = []
        for tag in sorted(self.sfnt.tables):
            rec = self.sfnt.tables[tag]
            actual = table_checksum(rec.data)
            summaries.append({
                "tag": tag,
                "offset": rec.offset,
                "length": rec.length,
                "checksum": "0x%08X" % rec.checksum,
                "checksum_ok": actual == rec.checksum or tag == "head",
            })
        return FontMeta(
            sfnt_version=self.sfnt.scaler,
            scaler_type="TrueType (quadratic)",
            num_glyphs=self.num_glyphs,
            units_per_em=self.head["units_per_em"],
            table_digest=self.digest[:16],
            table_summaries=summaries,
            names=dict(self.names),
            license_text=self.names.get(nametbl.NAME_LICENSE),
            license_url=self.names.get(nametbl.NAME_LICENSE_URL),
            family=(self.names.get(nametbl.NAME_TYPO_FAMILY)
                    or self.names.get(nametbl.NAME_FAMILY)),
            features=[{
                "tag": f["tag"],
                "scripts": f["scripts"],
                "default_on": f["default_on"],
                "lookup_count": len(f["lookup_indices"]),
            } for f in (self.gsub["features"] if self.gsub else [])],
            gpos_features=self.gpos_features,
            is_quadratic=True,
        )

    def glyph_by_gid(self, gid):
        if 0 <= gid < self.num_glyphs:
            return self.glyphs[gid]
        return None

    def lookup(self, cp):
        return self.cmap.get(cp)

    def variation_glyph(self, base_cp, variation_selector):
        """返回 (gid, kind)。

        kind:
          non_default: format14 显式映射
          default:     UVS 默认覆盖（glyph 来自 base cmap）
          none:        该 VS 未注册
        """
        rec = self.var_selectors.get(variation_selector)
        if rec is None:
            return None, "none"
        if rec.non_default_mappings:
            for cp, gid in rec.non_default_mappings:
                if cp == base_cp:
                    return gid, "non_default"
        if rec.default_ranges:
            for start, end in rec.default_ranges:
                if start <= base_cp <= end:
                    return self.cmap.get(base_cp), "default"
        return None, "none"
