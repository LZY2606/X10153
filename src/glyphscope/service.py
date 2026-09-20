"""面向 Web/CLI 的编排服务：上传、计划、导出、对比。"""

from typing import List, Optional

from .errors import PlanError
from .exporter import export_subset
from .planner import build_plan, sample_id
from .storage import Store


class GlyphScopeService:
    def __init__(self, home: Optional[str] = None):
        self.store = Store(home) if home else Store()

    def upload_font(self, data: bytes, filename: str) -> dict:
        return self.store.save_font(data, filename)

    def list_fonts(self) -> List[dict]:
        return self.store.list_fonts()

    def font_detail(self, digest: str) -> dict:
        meta = self.store.get_font_meta(digest)
        if meta is None:
            raise PlanError("字体不存在：%s" % digest)
        return meta

    def create_plan(
        self,
        font_sha256: str,
        texts: List[str],
        features: Optional[List[str]] = None,
        closure: str = "reachable",
        save: bool = True,
    ) -> dict:
        analysis = self.store.load_analysis(font_sha256)
        meta = self.store.get_font_meta(font_sha256)
        samples = [
            {"id": sample_id(text), "text": text}
            for text in texts
            if text != ""
        ]
        plan = build_plan(
            analysis,
            font_sha256,
            meta["filename"],
            samples,
            features=features,
            closure=closure,
        )
        if save:
            self.store.save_plan(plan)
        return plan

    def list_plans(self) -> List[dict]:
        return self.store.list_plans()

    def get_plan(self, plan_id: str) -> dict:
        return self.store.get_plan(plan_id)

    def export_plan(self, plan_id: str):
        plan = self.store.get_plan(plan_id)
        raw = self.store.raw_bytes(plan["font"]["sha256"])
        data, report = export_subset(raw, plan)
        path = self.store.save_export(plan_id, data, report, suffix=report["suffix"])
        return path, report

    def export_report(self, plan_id: str):
        return self.store.get_export_report(plan_id)

    def export_bytes(self, plan_id: str):
        path = self.store.export_path(plan_id)
        if path is None:
            return None
        with open(path, "rb") as fh:
            return fh.read()

    @staticmethod
    def compare_plans(plan_a: dict, plan_b: dict) -> dict:
        ga = {g["gid"]: g for g in plan_a["glyphs"]}
        gb = {g["gid"]: g for g in plan_b["glyphs"]}
        only_a = sorted(set(ga) - set(gb))
        only_b = sorted(set(gb) - set(ga))
        shared = sorted(set(ga) & set(gb))

        def gname(plan, gid):
            return next((g["name"] for g in plan["glyphs"] if g["gid"] == gid), None)

        return {
            "plan_a": plan_a["plan_id"],
            "plan_b": plan_b["plan_id"],
            "same_font": plan_a["font"]["sha256"] == plan_b["font"]["sha256"],
            "parameters_equal": plan_a["parameters"] == plan_b["parameters"],
            "samples_equal": plan_a["parameters"]["samples_fingerprint"]
            == plan_b["parameters"]["samples_fingerprint"],
            "counts": {
                "a": len(ga),
                "b": len(gb),
                "shared": len(shared),
                "only_a": len(only_a),
                "only_b": len(only_b),
            },
            "only_a": [{"gid": g, "name": gname(plan_a, g)} for g in only_a],
            "only_b": [{"gid": g, "name": gname(plan_b, g)} for g in only_b],
            "closure_a": plan_a["closure"],
            "closure_b": plan_b["closure"],
            "missing_a": plan_a["missing"],
            "missing_b": plan_b["missing"],
            "features_a": plan_a["parameters"]["features"],
            "features_b": plan_b["parameters"]["features"],
            "stats_a": plan_a["stats"],
            "stats_b": plan_b["stats"],
        }
