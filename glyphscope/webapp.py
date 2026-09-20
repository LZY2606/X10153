"""零依赖本地 Web 服务。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import FontCorrupt
from .planner import POLICY_CLOSURE
from .service import Service

STATIC_DIR = os.path.join(os.path.dirname(__file__), "web", "static")


def _parse_multipart(body, content_type):
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return {}
    boundary = m.group(1).strip('"')
    delimiter = ("--" + boundary).encode()
    fields = {}
    for part in body.split(delimiter):
        if not part or part in (b"--", b"--\r\n"):
            continue
        part = part.strip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        header_blob, content = part.split(b"\r\n\r\n", 1)
        headers = {}
        for line in header_blob.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower().decode()] = v.strip().decode()
        cd = headers.get("content-disposition", "")
        name_m = re.search(r'name="([^"]+)"', cd)
        file_m = re.search(r'filename="([^"]*)"', cd)
        if not name_m:
            continue
        name = name_m.group(1)
        if file_m:
            fields[name] = {
                "filename": file_m.group(1),
                "content": content,
                "content_type": headers.get("content-type", ""),
            }
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields


class Handler(BaseHTTPRequestHandler):
    service = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload, content_type="application/json"):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else \
                str(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/api/fonts":
                return self._send(200, {"fonts": self.service.storage.list_fonts()})
            if path == "/api/quarantine":
                return self._send(200, {"items": self.service.storage.list_quarantine()})
            if path == "/api/demo-font":
                from .fixtures import make_demo_font
                import base64 as _b64
                raw = make_demo_font()
                return self._send(200, {"data": _b64.b64encode(raw).decode("ascii")})
            m = re.match(r"^/api/fonts/([0-9a-f]{64})$", path)
            if m:
                digest = m.group(1)
                font = self.service.get_font(digest)
                return self._send(200, {"meta": self.service._meta_json(font)})
            m = re.match(r"^/api/fonts/([0-9a-f]{64})/plans$", path)
            if m:
                return self._send(200, {
                    "plans": self.service.list_plans(m.group(1))})
            m = re.match(r"^/api/fonts/([0-9a-f]{64})/plans/(\d+)$", path)
            if m:
                plan = self.service.get_plan(m.group(1), int(m.group(2)))
                return self._send(200, {"plan": plan})
            m = re.match(r"^/api/fonts/([0-9a-f]{64})/plans/(\d+)/graph$", path)
            if m:
                plan = self.service.get_plan(m.group(1), int(m.group(2)))
                return self._send(200, self.service.graph(plan))
            m = re.match(r"^/api/fonts/([0-9a-f]{64})/glyphs/(\d+)/svg$", path)
            if m:
                size = int(qs.get("size", ["120"])[0])
                svg = self.service.glyph_svg(m.group(1), m.group(2), size)
                return self._send(200, svg.encode("utf-8"), "image/svg+xml")
            m = re.match(r"^/api/fonts/([0-9a-f]{64})/plans/(\d+)/download$", path)
            if m:
                data, _p, _map, notes = self.service.export_plan(
                    m.group(1), int(m.group(2)))
                self.send_response(200)
                self.send_header("Content-Type", "font/ttf")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="subset-v%s.ttf"' % m.group(2))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send(404, {"error": "not found"})
        except KeyError as exc:
            return self._send(404, {"error": str(exc)})
        except FontCorrupt as exc:
            return self._send(400, {"error": str(exc)})
        except FileNotFoundError:
            return self._send(404, {"error": "字体不存在"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/fonts":
                return self._handle_upload()
            m = re.match(r"^/api/fonts/([0-9a-f]{64})/plans$", path)
            if m:
                return self._handle_create_plan(m.group(1))
            if path == "/api/compare":
                return self._handle_compare()
            return self._send(404, {"error": "not found"})
        except FontCorrupt as exc:
            return self._send(400, {"error": str(exc)})

    def _serve_static(self, name):
        # 防目录穿越
        name = os.path.normpath(name)
        if name.startswith("..") or os.path.isabs(name):
            return self._send(403, {"error": "forbidden"})
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_upload(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" in ctype:
            fields = _parse_multipart(body, ctype)
            file_field = fields.get("font") or fields.get("file")
            if not file_field:
                return self._send(400, {"error": "缺少字体文件"})
            raw = file_field["content"]
            filename = file_field["filename"]
        else:
            raw = body
            filename = "upload.ttf"
        result = self.service.upload(raw, filename)
        code = 200 if result["status"] in ("ok",) else 200
        return self._send(code, result)

    def _handle_create_plan(self, digest):
        data = self._read_json()
        samples = data.get("samples") or []
        if isinstance(samples, str):
            samples = [{"text": samples, "label": "样本 1"}]
        norm = []
        for i, item in enumerate(samples):
            if isinstance(item, str):
                norm.append({"text": item, "label": "样本 %d" % (i + 1)})
            else:
                norm.append({
                    "text": item.get("text", ""),
                    "label": item.get("label", "样本 %d" % (i + 1))})
        result = self.service.create_plan(
            digest, norm,
            feature_policy=data.get("feature_policy", POLICY_CLOSURE),
            features=data.get("features"),
            include_notdef=bool(data.get("include_notdef", False)),
            apply_alternates=bool(data.get("apply_alternates", False)),
            compose_combining=bool(data.get("compose_combining", True)),
            plan_only=bool(data.get("plan_only", False)))
        return self._send(200, result)

    def _handle_compare(self):
        data = self._read_json()
        plan_a = self.service.get_plan(data["font_digest_a"],
                                      int(data["version_a"]))
        if data.get("font_digest_b"):
            plan_b = self.service.get_plan(data["font_digest_b"],
                                          int(data["version_b"]))
        else:
            plan_b = self.service.get_plan(data["font_digest_a"],
                                          int(data["version_b"]))
        return self._send(200, self.service.compare(plan_a, plan_b))


def make_server(host, port, service=None):
    Handler.service = service or Service()
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd
