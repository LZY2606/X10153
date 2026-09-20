"""内容寻址的本地存储：原始字节、隔离区、计划版本。

目录结构（默认 ./.glyphscope）：
  fonts/<digest2>/font.bin                 原始字节
  fonts/<digest2>/meta.json                解析后元数据
  quarantine/<digest2>.bin                 损坏字体原件
  quarantine/<digest2>.json                隔离原因
  plans/<font_digest16>/<plan_digest16>/v<n>.json
  plans/<font_digest16>/<plan_digest16>/latest -> vN
  exports/<font_digest16>/<plan_digest16>-v<n>.ttf
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

from .errors import FontCorrupt


class Storage:
    def __init__(self, root=None):
        self.root = root or os.environ.get(
            "GLYPHSCOPE_HOME",
            os.path.join(os.getcwd(), ".glyphscope"))
        self.fonts_dir = os.path.join(self.root, "fonts")
        self.quarantine_dir = os.path.join(self.root, "quarantine")
        self.plans_dir = os.path.join(self.root, "plans")
        self.exports_dir = os.path.join(self.root, "exports")
        for d in (self.fonts_dir, self.quarantine_dir,
                  self.plans_dir, self.exports_dir):
            os.makedirs(d, exist_ok=True)

    # ---- 原始字节 ----
    def save_raw_font(self, raw):
        digest = hashlib.sha256(raw).hexdigest()
        d = os.path.join(self.fonts_dir, digest)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "font.bin")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(raw)
        return digest, path

    def load_raw_font(self, digest):
        path = os.path.join(self.fonts_dir, digest, "font.bin")
        with open(path, "rb") as fh:
            return fh.read()

    def list_fonts(self):
        out = []
        for digest in os.listdir(self.fonts_dir):
            meta_path = os.path.join(self.fonts_dir, digest, "meta.json")
            entry = {"digest": digest}
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as fh:
                    entry["meta"] = json.load(fh)
            out.append(entry)
        return sorted(out, key=lambda e: e["digest"])

    def save_font_meta(self, digest, meta):
        path = os.path.join(self.fonts_dir, digest, "meta.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)

    # ---- 隔离区 ----
    def quarantine(self, raw, reason, table=None, detail=None):
        digest = hashlib.sha256(raw).hexdigest()
        bin_path = os.path.join(self.quarantine_dir, digest + ".bin")
        if not os.path.exists(bin_path):
            with open(bin_path, "wb") as fh:
                fh.write(raw)
        record = {
            "digest": digest,
            "reason": reason,
            "table": table,
            "detail": detail,
            "quarantined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime()),
            "size": len(raw),
        }
        json_path = os.path.join(self.quarantine_dir, digest + ".json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        return record

    def list_quarantine(self):
        out = []
        for name in sorted(os.listdir(self.quarantine_dir)):
            if name.endswith(".json"):
                with open(os.path.join(self.quarantine_dir, name),
                          "r", encoding="utf-8") as fh:
                    out.append(json.load(fh))
        return out

    # ---- 计划版本 ----
    def _plan_dir(self, font_digest, content_hash):
        return os.path.join(self.plans_dir, font_digest[:16],
                            content_hash[:16])

    def save_plan(self, font_digest, plan):
        content_hash = plan["content_hash"]
        d = self._plan_dir(font_digest, content_hash)
        os.makedirs(d, exist_ok=True)

        existing_version = None
        for name in os.listdir(d):
            if name.startswith("v") and name.endswith(".json"):
                try:
                    ver = int(name[1:-5])
                except ValueError:
                    continue
                if existing_version is None or ver > existing_version:
                    existing_version = ver
        if existing_version is not None:
            # 完全相同的内容：旧计划保持不变，返回既有版本以保证可重现
            version = existing_version
        else:
            version = self._next_global_version(font_digest)

        file_name = "v%d.json" % version
        path = os.path.join(d, file_name)
        record = dict(plan)
        record["font_digest"] = font_digest
        record["version"] = version
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2,
                      sort_keys=False)
        latest = os.path.join(d, "latest")
        with open(latest, "w", encoding="utf-8") as fh:
            fh.write(file_name)
        return version, path

    def _next_global_version(self, font_digest):
        base = os.path.join(self.plans_dir, font_digest[:16])
        max_ver = 0
        if os.path.isdir(base):
            for root, _dirs, files in os.walk(base):
                for name in files:
                    if name.startswith("v") and name.endswith(".json"):
                        try:
                            max_ver = max(max_ver, int(name[1:-5]))
                        except ValueError:
                            pass
        return max_ver + 1

    def load_plan(self, font_digest, version):
        base = os.path.join(self.plans_dir, font_digest[:16])
        if not os.path.isdir(base):
            raise KeyError("没有该字体的计划")
        for root, _dirs, files in os.walk(base):
            target = "v%d.json" % version
            if target in files:
                with open(os.path.join(root, target), "r",
                          encoding="utf-8") as fh:
                    return json.load(fh)
        raise KeyError("计划版本 v%d 不存在" % version)

    def list_plans(self, font_digest):
        base = os.path.join(self.plans_dir, font_digest[:16])
        out = []
        if os.path.isdir(base):
            for root, _dirs, files in os.walk(base):
                for name in files:
                    if not (name.startswith("v") and name.endswith(".json")):
                        continue
                    path = os.path.join(root, name)
                    with open(path, "r", encoding="utf-8") as fh:
                        plan = json.load(fh)
                    out.append({
                        "version": plan.get("version"),
                        "content_hash": plan.get("content_hash"),
                        "created_at": plan.get("created_at"),
                        "samples": plan.get("samples"),
                        "strategy": plan.get("strategy"),
                        "stats": plan.get("stats"),
                        "path": path,
                    })
        out.sort(key=lambda p: p["version"])
        return out

    def save_export(self, font_digest, content_hash, version, data):
        d = os.path.join(self.exports_dir, font_digest[:16])
        os.makedirs(d, exist_ok=True)
        name = "%s-v%d.ttf" % (content_hash[:16], version)
        path = os.path.join(d, name)
        with open(path, "w+b") as fh:
            fh.write(data)
        return path
