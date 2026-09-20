"""字形子集检查站 Web 界面。

页面：
  /                 首页：上传、字体/计划列表
  /fonts/<sha>      表摘要、许可证、隔离原因
  /plans/new        选择字体、输入样本、特性与闭合策略
  /plans/<id>       计划详情：glyph 溯源、缺失/.notdef、布局动作、本地预览
  /plans/<id>/graph 字符 -> glyph 关系图
  /compare          对比两个计划
所有下载都回到具体计划的 JSON / 子集文件。
"""

import io
import json
import os
from typing import Optional

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from ..errors import GlyphScopeError, PlanError, QuarantinedFontError
from ..preview import relation_graph, render_sample_svg
from ..service import GlyphScopeService


def create_app(home: Optional[str] = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "glyphscope-local-checkpoint"
    service = GlyphScopeService(home)
    app.config["SERVICE"] = service

    @app.context_processor
    def inject_helpers():
        return {"enumerate": enumerate}

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            fonts=service.list_fonts(),
            plans=service.list_plans(),
        )

    @app.route("/upload", methods=["POST"])
    def upload():
        upload_obj = request.files.get("font")
        if upload_obj is None or not upload_obj.filename:
            flash("请选择一个 .ttf/.otf 字体文件。", "error")
            return redirect(url_for("index"))
        data = upload_obj.read()
        if len(data) == 0:
            flash("上传文件为空。", "error")
            return redirect(url_for("index"))
        meta = service.upload_font(data, os.path.basename(upload_obj.filename))
        if meta.get("quarantined"):
            flash(
                "字体已隔离：%s" % meta.get("quarantine_reason"),
                "error",
            )
            return redirect(url_for("font_detail", sha=meta["sha256"]))
        flash("字体已保存原始字节并通过解析：%s" % meta["filename"], "ok")
        return redirect(url_for("font_detail", sha=meta["sha256"]))

    @app.route("/fonts/<sha>")
    def font_detail(sha):
        meta = service.font_detail(sha)
        return render_template("font_detail.html", meta=meta)

    @app.route("/fonts/<sha>/raw")
    def font_raw(sha):
        meta = service.font_detail(sha)
        data = service.store.raw_bytes(sha)
        return send_file(
            io.BytesIO(data),
            mimetype="font/ttf",
            as_attachment=True,
            download_name=meta["filename"],
        )

    @app.route("/plans/new", methods=["GET", "POST"])
    def plan_new():
        if request.method == "POST":
            sha = request.form.get("font_sha256", "")
            raw_texts = request.form.get("samples", "")
            texts = [line.strip() for line in raw_texts.splitlines() if line.strip()]
            features = request.form.getlist("features")
            closure = request.form.get("closure", "reachable")
            if not texts:
                flash("请至少输入一行文本样本。", "error")
                return redirect(url_for("plan_new"))
            try:
                plan = service.create_plan(sha, texts, features or None, closure)
            except QuarantinedFontError as exc:
                flash(str(exc), "error")
                return redirect(url_for("font_detail", sha=sha))
            except GlyphScopeError as exc:
                flash(str(exc), "error")
                return redirect(url_for("plan_new"))
            if request.form.get("action") == "export":
                try:
                    service.export_plan(plan["plan_id"])
                    flash("子集已导出。", "ok")
                except GlyphScopeError as exc:
                    flash("导出失败：%s" % exc, "error")
            else:
                flash("计划已生成（仅计划，未导出子集文件）。", "ok")
            return redirect(url_for("plan_detail", plan_id=plan["plan_id"]))
        sha = request.args.get("font", "")
        fonts = [f for f in service.list_fonts() if not f.get("quarantined")]
        return render_template(
            "plan_new.html",
            fonts=fonts,
            selected=sha,
            default_features=["calt", "liga", "clig", "rlig", "ccmp"],
            closure_modes=[
                ("none", "none：不做特性外推"),
                ("reachable", "reachable：纳入样本可静态到达的替代目标（默认）"),
                ("full", "full：纳入特性涉及的全部替代目标"),
            ],
            all_features=["calt", "liga", "clig", "rlig", "ccmp", "locl", "kern", "mark", "mkmk", "rclt"],
        )

    @app.route("/plans/<plan_id>")
    def plan_detail(plan_id):
        plan = service.get_plan(plan_id)
        report = service.export_report(plan_id)
        previews = None
        if not plan["font"]["sha256"]:
            abort(404)
        try:
            analysis = service.store.load_analysis(plan["font"]["sha256"])
            previews = [
                render_sample_svg(analysis, sample)
                for sample in plan["sample_shaping"]
            ]
        except Exception:
            previews = []
        return render_template(
            "plan_detail.html",
            plan=plan,
            report=report,
            previews=previews,
        )

    @app.route("/plans/<plan_id>/graph")
    def plan_graph(plan_id):
        plan = service.get_plan(plan_id)
        graph = relation_graph(plan)
        return render_template("graph.html", plan=plan, graph=graph)

    @app.route("/plans/<plan_id>/json")
    def plan_json(plan_id):
        plan = service.get_plan(plan_id)
        payload = json.dumps(plan, ensure_ascii=False, indent=2)
        return Response(
            payload,
            mimetype="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="plan-%s.json"' % plan_id
            },
        )

    @app.route("/plans/<plan_id>/export", methods=["POST"])
    def plan_export(plan_id):
        service.get_plan(plan_id)
        try:
            _path, report = service.export_plan(plan_id)
        except GlyphScopeError as exc:
            flash("导出失败：%s" % exc, "error")
            return redirect(url_for("plan_detail", plan_id=plan_id))
        flash("子集导出完成，实际 %d 个 glyph。" % report["exported_glyph_count"], "ok")
        return redirect(url_for("plan_detail", plan_id=plan_id))

    @app.route("/plans/<plan_id>/download")
    def plan_download(plan_id):
        report = service.export_report(plan_id)
        data = service.export_bytes(plan_id)
        if data is None:
            abort(404)
        plan = service.get_plan(plan_id)
        suffix = report["suffix"] if report else ".ttf"
        return send_file(
            io.BytesIO(data),
            mimetype="font/ttf" if suffix == ".ttf" else "font/otf",
            as_attachment=True,
            download_name="%s-subset%s" % (plan["font"]["filename"].rsplit(".", 1)[0], suffix),
        )

    @app.route("/compare", methods=["GET", "POST"])
    def compare():
        plans = service.list_plans()
        result = None
        if request.method == "POST":
            a = request.form.get("plan_a", "")
            b = request.form.get("plan_b", "")
            if a and b and a != b:
                result = service.compare_plans(service.get_plan(a), service.get_plan(b))
            else:
                flash("请选择两个不同的计划。", "error")
        return render_template("compare.html", plans=plans, result=result)

    @app.errorhandler(PlanError)
    def handle_plan_error(exc):
        return render_template("error.html", message=str(exc)), 404

    return app
