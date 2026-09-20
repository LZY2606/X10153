"use strict";

const state = {
  digest: null,
  meta: null,
  plan: null,
  plans: [],
  result: null,
};

const $ = (id) => document.getElementById(id);

function setStatus(el, msg, kind) {
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}

async function api(path, options) {
  const res = await fetch(path, options);
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }
  return res;
}

function cpLabel(cp) {
  return cp > 0xFFFF ? "U+" + cp.toString(16).toUpperCase().padStart(5, "0")
    : "U+" + cp.toString(16).toUpperCase().padStart(4, "0");
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

$("upload-btn").addEventListener("click", uploadFont);
$("demo-btn").addEventListener("click", loadDemo);

async function uploadFont() {
  const file = $("font-file").files[0];
  if (!file) {
    setStatus($("upload-status"), "请先选择 .ttf 文件", "err");
    return;
  }
  const fd = new FormData();
  fd.append("font", file);
  setStatus($("upload-status"), "解析中…");
  try {
    const result = await api("/api/fonts", { method: "POST", body: fd });
    handleUploadResult(result, file.name);
  } catch (e) {
    setStatus($("upload-status"), "上传失败：" + e.message, "err");
  }
}

async function loadDemo() {
  setStatus($("upload-status"), "生成演示夹具并解析…");
  const data = await fetch("/static/demo.bin");
  // demo.bin 由服务器端在首次需要时生成（见 fallback），否则用内置构造接口
  let blob;
  if (data.ok) {
    blob = await data.blob();
  } else {
    // 通过 API 生成（测试/演示）
    const gen = await api("/api/demo-font");
    const u8 = Uint8Array.from(atob(gen.data), (c) => c.charCodeAt(0));
    blob = new Blob([u8], { type: "font/ttf" });
  }
  const fd = new FormData();
  fd.append("font", blob, "GlyphScopeDemo.ttf");
  const result = await api("/api/fonts", { method: "POST", body: fd });
  handleUploadResult(result, "GlyphScopeDemo.ttf");
}

function handleUploadResult(result, filename) {
  const qbox = $("quarantine-box");
  if (result.status === "quarantined") {
    qbox.classList.remove("hidden");
    qbox.innerHTML = "<strong>字体已进入隔离区</strong><br>" +
      "原因：" + esc(result.quarantine.reason) +
      (result.quarantine.table ? "（表 " + esc(result.quarantine.table) + "）" : "") +
      "<br>摘要 " + esc(result.digest.slice(0, 16)) +
      "<br>系统不会使用半棵依赖树继续处理。";
    setStatus($("upload-status"), "解析失败，字体被隔离", "err");
    return;
  }
  if (result.status === "unsupported") {
    qbox.classList.remove("hidden");
    qbox.textContent = "字体类型暂不支持（仅支持 TrueType 轮廓）：" +
      esc(result.quarantine.reason);
    setStatus($("upload-status"), "不支持的字体", "err");
    return;
  }
  qbox.classList.add("hidden");
  state.digest = result.digest;
  state.meta = result.meta;
  setStatus($("upload-status"),
    "已保存原始字节并完成全表校验：" + filename + "（" +
    result.meta.num_glyphs + 个 glyph）", "ok");
  renderFontMeta();
  $("font-panel").classList.remove("hidden");
  $("sample-panel").classList.remove("hidden");
  $("result-panel").classList.add("hidden");
  $("compare-panel").classList.add("hidden");
  initSamples();
  initFeatures();
  loadPlanList();
}

function renderFontMeta() {
  const m = state.meta;
  $("font-summary").innerHTML =
    "<div><strong>" + esc(m.family || "(未命名字体)") + "</strong></div>" +
    "<div class='muted'>" + esc(m.scaler_type) + " · unitsPerEm=" +
    m.units_per_em + " · glyph=" + m.num_glyphs +
    " · digest " + esc(m.table_digest) + "</div>" +
    "<div class='muted'>许可证：" +
    (m.license_url
      ? "<a href='" + esc(m.license_url) + "' target='_blank'>" +
        esc(m.license_url) + "</a>"
      : "（无 URL）") + "</div>";
  $("name-meta").textContent =
    (m.license_text ? "LICENSE: " + m.license_text + "\n\n" : "") +
    Object.entries(m.names || {})
      .map(([k, v]) => "nameID " + k + ": " + v).join("\n");
  const tbody = $("table-list").querySelector("tbody");
  tbody.innerHTML = m.tables.map((t) =>
    "<tr><td>" + esc(t.tag) + "</td><td>" + t.offset + "</td><td>" +
    t.length + "</td><td>" + esc(t.checksum) + "</td><td>" +
    (t.checksum_ok ? "✓" : "✗") + "</td></tr>").join("");
  const fl = $("feature-list");
  if (!m.features.length) {
    fl.innerHTML = "<span class='muted'>(无 GSUB 特性)</span>";
  } else {
    fl.innerHTML = m.features.map((f) =>
      "<span class='origin-tag layout'>" + esc(f.tag) +
      " ×" + f.lookup_count +
      (f.default_on ? " ·默认" : "") + "</span>").join("") +
      (m.gpos_features.length
        ? "<div class='muted'>GPOS 特性：" +
          m.gpos_features.map(esc).join(", ") + "</div>" : "");
  }
}

function initSamples() {
  const box = $("samples");
  box.innerHTML = "";
  addSampleRow("AfiB A\ufe00 \u00c1", "拉丁/连字/VS/组合");
  addSampleRow("\U00020492", "代理平面");
}

function addSampleRow(text, label) {
  const wrap = document.createElement("div");
  wrap.className = "sample-row";
  wrap.innerHTML =
    "<div class='sample-head'><input type='text' class='s-label' " +
    "style='width:200px' value='" + esc(label || "") + "'>" +
    "<button class='secondary remove-sample'>移除</button></div>" +
    "<textarea class='s-text'>" + esc(text || "") + "</textarea>";
  wrap.querySelector(".remove-sample").addEventListener("click", () =>
    wrap.remove());
  $("samples").appendChild(wrap);
}
$("add-sample").addEventListener("click", () => addSampleRow("", ""));

function initFeatures() {
  const m = state.meta;
  const tags = Array.from(new Set(
    (m.features || []).map((f) => f.tag)));
  const defaults = ["liga", "ccmp", "rlig", "clig", "calt"];
  const box = $("feature-toggles");
  box.innerHTML = "";
  tags.forEach((tag) => {
    const id = "feat-" + tag;
    const lbl = document.createElement("label");
    lbl.innerHTML = "<input type='checkbox' id='" + id + "' " +
      (defaults.includes(tag) ? "checked" : "") + "> " + esc(tag);
    box.appendChild(lbl);
  });
}

function selectedFeatures() {
  return Array.from($("feature-toggles")
    .querySelectorAll("input[type=checkbox]"))
    .filter((c) => c.checked)
    .map((c) => c.id.slice(5));
}

$("plan-btn").addEventListener("click", createPlan);

async function createPlan() {
  const samples = Array.from($("samples").children).map((row, i) => ({
    label: row.querySelector(".s-label").value || ("样本 " + (i + 1)),
    text: row.querySelector(".s-text").value,
  })).filter((s) => s.text.length);
  if (!samples.length) {
    setStatus($("plan-status"), "请输入至少一段文本", "err");
    return;
  }
  setStatus($("plan-status"), "生成计划中…");
  const payload = {
    samples,
    feature_policy: $("feature-policy").value,
    features: selectedFeatures(),
    include_notdef: $("include-notdef").checked,
    plan_only: $("plan-only").checked,
  };
  try {
    const result = await api(
      "/api/fonts/" + state.digest + "/plans",
      { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload) });
    state.result = result;
    state.plan = result.plan;
    setStatus($("plan-status"),
      "计划 v" + result.version + " 已保存（旧版本仍可重现）", "ok");
    renderResult();
    await loadPlanList();
  } catch (e) {
    setStatus($("plan-status"), "失败：" + e.message, "err");
  }
}

function renderResult() {
  const p = state.plan;
  $("result-panel").classList.remove("hidden");
  $("compare-panel").classList.remove("hidden");
  const s = p.stats;
  $("plan-meta").innerHTML =
    "版本 <strong>v" + state.result.version + "</strong> · " +
    "content_hash " + esc(p.content_hash.slice(0, 12)) + " · " +
    "子集 glyph " + s.num_glyphs_in_subset + "/" + s.num_glyphs_in_font +
    " · 复合 " + s.num_composite_glyphs +
    " · 替代 " + s.num_layout_substitutions +
    " · 缺失 " + s.num_missing_chars +
    " · .notdef " + s.num_notdef_chars;
  const exp = state.result.export;
  $("export-info").textContent = exp
    ? "已导出 TTF：" + exp.size + " 字节，许可证元数据随 name 表保留"
    : "当前为仅计划模式，未导出字体；可在版本列表中重新导出";
  renderPreview();
  renderGlyphTable();
  renderGraph();
}

function originTag(o) {
  const map = {
    cmap: "字符",
    variation_non_default: "VS非默认",
    variation_default: "VS默认",
    variation_unregistered: "VS未注册",
    variation_base_dependency: "VS基字",
    composite_dependency: "复合组件",
    layout_single: "单替代",
    layout_multiple: "多替代",
    layout_alternate: "替代字形",
    layout_ligature: "连字",
    feature_closure: "特性闭包",
    policy_include_notdef: ".notdef策略",
  };
  let detail = map[o.kind] || o.kind;
  if (o.feature) detail += "(" + o.feature + ")";
  if (o.cp_label) detail += " " + o.cp_label;
  if (o.parent_name) detail += "←" + o.parent_name;
  const cls = o.kind.startsWith("composite") ? "composite"
    : o.kind.startsWith("layout") ? "layout"
    : o.kind === "feature_closure" ? "feature_closure"
    : o.kind.startsWith("variation") ? "variation"
    : o.kind === "missing_mapping" ? "missing" : "";
  return "<span class='origin-tag " + cls + "' title='" +
    esc(JSON.stringify(o)) + "'>" + esc(detail) + "</span>";
}

function renderPreview() {
  const box = $("preview");
  box.innerHTML = "";
  state.plan.preview_tokens.forEach((t) => {
    const div = document.createElement("div");
    div.className = "preview-token " + t.status;
    const ch = esc(t.chars || "");
    let flag = "";
    if (t.status === "missing") flag = "<div class='flag'>缺失映射</div>";
    if (t.status === "notdef") flag = "<div class='flag'>.notdef</div>";
    if (t.status === "absorbed") flag = "<div class='flag'>被连字吸收</div>";
    const lig = t.substitution_chain
      .filter((c) => c.kind === "layout_ligature")
      .map((c) => c.feature_tag).join(",");
    if (lig) flag += "<div class='flag'>连字 " + esc(lig) + "</div>";
    div.innerHTML = "<div class='ch'>" + ch + "</div>" +
      "<div class='gid'>" + t.glyphs.map((g) => "g" + g).join(",") +
      "</div>" + flag;
    box.appendChild(div);
  });
}

async function renderGlyphTable() {
  const tbody = $("glyph-table").querySelector("tbody");
  const rows = state.plan.glyphs.map(async (g) => {
    const svgUrl = "/api/fonts/" + state.digest +
      "/glyphs/" + g.glyph_index + "/svg?size=40";
    const origins = g.origins.map(originTag).join("");
    return "<tr><td>" + g.glyph_index + "</td>" +
      "<td><img class='glyph-thumb' src='" + svgUrl +
      "' width='34' height='34'></td>" +
      "<td>" + esc(g.name) + (g.is_composite ? " ◈" : "") + "</td>" +
      "<td>" + g.included_by.join(", ") + "</td>" +
      "<td>" + origins + "</td></tr>";
  });
  tbody.innerHTML = (await Promise.all(rows)).join("");
}

async function renderGraph() {
  const g = await api("/api/fonts/" + state.digest + "/plans/" +
    state.result.version + "/graph");
  const svg = $("graph");
  svg.innerHTML = "";
  const W = Math.max(900, g.nodes.length * 70);
  const H = 420;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);

  const columns = { char: 60, glyph: 0, missing: 60 };
  const glyphGids = g.nodes.filter((n) => n.kind === "glyph")
    .map((n) => n.glyph_index).sort((a, b) => a - b);
  const rows = {};
  glyphGids.forEach((gid, i) => rows[gid] = 70 + i * 40);
  const charNodes = g.nodes.filter((n) => n.kind === "char");
  const missNodes = g.nodes.filter((n) => n.kind === "missing");
  const pos = {};
  charNodes.forEach((n, i) => pos[n.id] = [60, 90 + i * 46]);
  missNodes.forEach((n, i) => pos[n.id] = [60, 90 + (charNodes.length + i) * 46]);
  g.nodes.filter((n) => n.kind === "glyph").forEach((n) => {
    pos[n.id] = [W / 2 + 120, rows[n.glyph_index]];
  });

  const NS = "http://www.w3.org/2000/svg";
  // 箭头标记
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML =
    "<marker id='arrow' markerWidth='8' markerHeight='8' refX='7' " +
    "refY='3' orient='auto'><path d='M0,0 L7,3 L0,6 Z' fill='#9ca3af'/>" +
    "</marker>";
  svg.appendChild(defs);

  g.links.forEach((l) => {
    const a = pos[l.source], b = pos[l.target];
    if (!a || !b) return;
    const path = document.createElementNS(NS, "path");
    const mid = (a[0] + b[0]) / 2;
    path.setAttribute("d",
      "M" + a[0] + "," + a[1] + " C" + mid + "," + a[1] + " " +
      mid + "," + b[1] + " " + b[0] + "," + b[1]);
    path.setAttribute("class", "link " + (l.kind || ""));
    path.setAttribute("marker-end", "url(#arrow)");
    const title = l.kind + (l.feature ? " (" + l.feature + ")" : "");
    const t = document.createElementNS(NS, "title");
    t.textContent = title;
    path.appendChild(t);
    svg.appendChild(path);
  });

  g.nodes.forEach((n) => {
    const [x, y] = pos[n.id];
    const gEl = document.createElementNS(NS, "g");
    gEl.setAttribute("class", "node-" + n.kind);
    const w = Math.max(78, n.label.length * 6.4 + 12);
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", x - w / 2);
    rect.setAttribute("y", y - 14);
    rect.setAttribute("width", w);
    rect.setAttribute("height", 26);
    rect.setAttribute("rx", 6);
    const text = document.createElementNS(NS, "text");
    text.setAttribute("x", x);
    text.setAttribute("y", y + 3);
    text.setAttribute("text-anchor", "middle");
    text.textContent = n.label;
    gEl.appendChild(rect);
    gEl.appendChild(text);
    svg.appendChild(gEl);
  });
}

async function loadPlanList() {
  const data = await api("/api/fonts/" + state.digest + "/plans");
  state.plans = data.plans;
  const opts = data.plans.map((p) =>
    "<option value='" + p.version + "'>v" + p.version + " · " +
    esc(p.content_hash.slice(0, 8)) + " · glyph " +
    (p.stats ? p.stats.num_glyphs_in_subset : "?") + "</option>").join("");
  $("version-select").innerHTML = opts;
  $("cmp-a").innerHTML = opts;
  $("cmp-b").innerHTML = opts;
  if (data.plans.length > 1) {
    $("cmp-b").selectedIndex = data.plans.length - 1;
  }
}

$("load-version").addEventListener("click", async () => {
  const v = $("version-select").value;
  const data = await api("/api/fonts/" + state.digest + "/plans/" + v);
  state.plan = data.plan;
  state.result = { version: data.plan.version, plan: data.plan,
    export: { size: 0 } };
  // export 元数据延迟获取
  renderResult();
});

$("download-font").addEventListener("click", () => {
  const v = state.result.version;
  window.location = "/api/fonts/" + state.digest +
    "/plans/" + v + "/download";
});

$("download-json").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(state.plan, null, 2)],
    { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "glyphscope-plan-v" + state.result.version + ".json";
  a.click();
});

$("cmp-btn").addEventListener("click", async () => {
  const body = {
    font_digest_a: state.digest,
    version_a: parseInt($("cmp-a").value, 10),
    version_b: parseInt($("cmp-b").value, 10),
  };
  const r = await api("/api/compare", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  $("cmp-result").innerHTML =
    "<div class='cmp-section'><strong>仅 A 有：</strong>" +
    r.only_in_a.map((g) => g.glyph_index + " " + g.name).join(", ") +
    "</div><div class='cmp-section'><strong>仅 B 有：</strong>" +
    r.only_in_b.map((g) => g.glyph_index + " " + g.name).join(", ") +
    "</div><div class='cmp-section'><strong>共同 glyph：</strong>" +
    r.common_count + "；追溯来源变化：" + r.changed_origins.length +
    "</div>" + r.changed_origins.map((c) =>
      "<div class='muted'>gid" + c.glyph_index + " " + esc(c.name) +
      "</div>").join("");
});
