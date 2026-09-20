"""内容寻址的本地存储。

目录结构（默认 ``.glyphscope``，可用 ``$GLYPHSCOPE_HOME`` 覆盖）：

  fonts/<sha256>/original.<ext>   上传的原始字节（不可变）
  fonts/<sha256>/meta.json        解析摘要或隔离记录
  plans/<plan_id>.json            版本化子集计划（不可变、可复现）
  exports/<plan_id>/subset.ttf    实际导出的子集文件
  exports/<plan_id>/report.json   导出报告（实际 glyph、许可证核对）

关键原则：原始字节总是先保存，再解析；解析失败只写隔离记录，
不产生任何依赖树。
"""

import json
import os
import time
from typing import List, Optional

from .errors import FontCorruptError, PlanError, QuarantinedFontError
from .loader import analyze_bytes, sha256_hex

DEFAULT_HOME = os.environ.get("GLYPHSCOPE_HOME") or os.path.join(
    os.getcwd(), ".glyphscope"
)


class Store:
    def __init__(self, home: str = DEFAULT_HOME):
        self.home = home
        self.fonts_dir = os.path.join(home, "fonts")
        self.plans_dir = os.path.join(home, "plans")
        self.exports_dir = os.path.join(home, "exports")
        for path in (self.fonts_dir, self.plans_dir, self.exports_dir):
            os.makedirs(path, exist_ok=True)

    # ---------- 字体 ----------

    def font_dir(self, digest: str) -> str:
        return os.path.join(self.fonts_dir, digest)

    def font_path(self, digest: str) -> Optional[str]:
        directory = self.font_dir(digest)
        if not os.path.isdir(directory):
            return None
        for name in os.listdir(directory):
            if name.startswith("original."):
                return os.path.join(directory, name)
        return None

    def list_fonts(self) -> List[dict]:
        out = []
        if not os.path.isdir(self.fonts_dir):
            return out
        for digest in sorted(os.listdir(self.fonts_dir)):
            meta = self._read_json(os.path.join(self.fonts_dir, digest, "meta.json"))
            if meta is not None:
                out.append(meta)
        return out

    def get_font_meta(self, digest: str) -> Optional[dict]:
        return self._read_json(os.path.join(self.font_dir(digest), "meta.json"))

    def save_font(self, data: bytes, filename: str) -> dict:
        """先保存原始字节，再解析；任何损坏都登记为隔离。"""
        digest = sha256_hex(data)
        directory = self.font_dir(digest)
        os.makedirs(directory, exist_ok=True)
        ext = os.path.splitext(filename)[1]
        if not ext or len(ext) > 10:
            ext = ".bin"
        raw_path = os.path.join(directory, "original" + ext)
        if not os.path.exists(raw_path):
            with open(raw_path, "wb") as fh:
                fh.write(data)
        meta_path = os.path.join(directory, "meta.json")
        if os.path.exists(meta_path):
            return self._read_json(meta_path)
        try:
            analysis = analyze_bytes(data)
            meta = {
                "sha256": digest,
                "filename": filename,
                "size": len(data),
                "quarantined": False,
                "flavor": analysis.flavor,
                "num_glyphs": analysis.num_glyphs,
                "has_gsub": analysis.has_gsub,
                "has_gpos": analysis.has_gpos,
                "has_glyf": analysis.has_glyf,
                "has_cff": analysis.has_cff,
                "tables": analysis.table_summary,
                "licenses": analysis.licenses,
                "stored_at": int(time.time()),
            }
        except FontCorruptError as exc:
            meta = {
                "sha256": digest,
                "filename": filename,
                "size": len(data),
                "quarantined": True,
                "quarantine_reason": str(exc),
                "quarantine_table": exc.table,
                "stored_at": int(time.time()),
            }
        self._write_json(meta_path, meta)
        return meta

    def load_analysis(self, digest: str):
        """重新解析已存字体（供计划与导出使用）；隔离字体直接拒绝。"""
        meta = self.get_font_meta(digest)
        if meta is None:
            raise PlanError("字体不存在：%s" % digest)
        if meta.get("quarantined"):
            raise QuarantinedFontError(
                "字体 %s 已隔离：%s" % (digest, meta.get("quarantine_reason"))
            )
        path = self.font_path(digest)
        if path is None:
            raise PlanError("字体原始字节丢失：%s" % digest)
        with open(path, "rb") as fh:
            return analyze_bytes(fh.read())

    def raw_bytes(self, digest: str) -> bytes:
        path = self.font_path(digest)
        if path is None:
            raise PlanError("字体不存在：%s" % digest)
        with open(path, "rb") as fh:
            return fh.read()

    # ---------- 计划 ----------

    def plan_path(self, plan_id: str) -> str:
        return os.path.join(self.plans_dir, plan_id + ".json")

    def list_plans(self) -> List[dict]:
        out = []
        if not os.path.isdir(self.plans_dir):
            return out
        for name in sorted(os.listdir(self.plans_dir)):
            if not name.endswith(".json"):
                continue
            plan = self._read_json(os.path.join(self.plans_dir, name))
            if plan is not None:
                out.append(self._plan_listing(plan))
        return out

    @staticmethod
    def _plan_listing(plan: dict) -> dict:
        return {
            "plan_id": plan["plan_id"],
            "spec_version": plan["spec_version"],
            "font_sha256": plan["font"]["sha256"],
            "font_filename": plan["font"]["filename"],
            "closure": plan["parameters"]["closure"],
            "features": plan["parameters"]["features"],
            "stats": plan["stats"],
            "num_samples": len(plan["samples"]),
        }

    def get_plan(self, plan_id: str) -> dict:
        plan = self._read_json(self.plan_path(plan_id))
        if plan is None:
            raise PlanError("计划不存在：%s" % plan_id)
        return plan

    def save_plan(self, plan: dict, overwrite: bool = False) -> str:
        path = self.plan_path(plan["plan_id"])
        if os.path.exists(path) and not overwrite:
            return plan["plan_id"]
        self._write_json(path, plan)
        return plan["plan_id"]

    # ---------- 导出 ----------

    def export_dir(self, plan_id: str) -> str:
        return os.path.join(self.exports_dir, plan_id)

    def get_export_report(self, plan_id: str) -> Optional[dict]:
        return self._read_json(os.path.join(self.export_dir(plan_id), "report.json"))

    def save_export(self, plan_id: str, subset_bytes: bytes, report: dict,
                    suffix: str = ".ttf"):
        directory = self.export_dir(plan_id)
        os.makedirs(directory, exist_ok=True)
        font_out = os.path.join(directory, "subset" + suffix)
        with open(font_out, "wb") as fh:
            fh.write(subset_bytes)
        self._write_json(os.path.join(directory, "report.json"), report)
        return font_out

    def export_path(self, plan_id: str) -> Optional[str]:
        directory = self.export_dir(plan_id)
        if not os.path.isdir(directory):
            return None
        for name in ("subset.ttf", "subset.otf"):
            path = os.path.join(directory, name)
            if os.path.exists(path):
                return path
        return None

    # ---------- JSON 工具 ----------

    @staticmethod
    def _write_json(path: str, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: str):
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
