# GlyphScope · 字形子集检查站

离线（纯 Python 标准库）分析 TrueType 字体：上传字体与多语言文本样本后，
读取 `cmap`、glyph 依赖、复合组件与布局关系，生成**候选 glyph 集合**，
并把每个 glyph 追溯到触发它的字符、variation selector、组合关系或替代规则。
支持确定性子集导出、计划版本化与计划对比。

## 安装与演示

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'

.venv/bin/pytest -q
.venv/bin/python -m glyphscope --host 127.0.0.1 --port 5230
```

打开 http://127.0.0.1:5230 ，页面标题为「字形子集检查站」。
点击「载入演示夹具」即可使用仓库代码构造的 MIT 小型字体，无需外部资源。

## 核心概念

- **缺失映射 vs 映射到 `.notdef`**：
  - 字符无 `cmap` 映射 → `missing`，不会产生任何业务 glyph。
  - 字符显式映射到 gid 0 → `notdef`，与缺失明确区分。
  - gid 0（`.notdef`）作为 TrueType 子集约定始终保留为新 gid 0。
- **Variation Selector**：支持 format 14 的默认 UVS（glyph 来自 base cmap）
  与非默认 UVS（显式 glyph）。非默认映射同时把基字 glyph 作为依赖保留，
  来源标记为 `variation_base_dependency`。VS 未注册时回退 base cmap 并记录。
- **复合 glyph**：加载时全量解析组件并做**传递闭包 + 防环检测**，
  发现循环立即判定字体损坏，整份字体进入隔离区，绝不带半棵树继续。
- **布局特性策略**：
  - `none`：不应用、不保留布局特性。
  - `base`：应用样本直接触发的替代，保留启用的 GSUB 特性。
  - `closure`（默认）：额外纳入「保留该特性所需、但样本没有直接触发」
    的 glyph（例如 coverage、连字组件/结果、单替代/多替代/替代目标），
    来源标记为 `feature_closure`。
  - 连字在**全局 glyph 流**上模拟，因此可跨字符聚类；被连字吸收的字符
    在预览中标记「被连字吸收」。
- **确定性**：glyph 按 gid 升序输出；导出无时间戳，表按 tag 排序，
  同样输入得到字节一致的 TTF 与相同 `content_hash`。

## 计划版本与可重现性

- 原始字体字节按 sha256 内容寻址保存（`fonts/<digest>/font.bin`）。
- 计划按「字体 + 样本 + 策略 + 候选 gid」规范化哈希得到 `content_hash`，
  并分配字体范围内递增的版本号。
- 改变样本或策略会产生新版本，**旧版本计划文件永不覆盖**，可随时取回、
  重新导出，保证可重现。
- 可选择「只生成计划」（`plan_only`），稍后再导出。

## 隔离策略

以下情况在**加载阶段全量校验**时直接判定 `FontCorrupt`，整份字体进入隔离区，
保存原始字节与原因，不会建立部分依赖树：

- 非法/越界 sfnt 表 offset、重复或非法表标签
- `cmap` 越界子表 offset、非法 segment / glyphIdArray / format14
- `glyf` / `loca` 越界、非单调 loca、复合 glyph 组件 gid 越界
- 复合 glyph 组件依赖环
- `GSUB` 非法 coverage、offset 或不被支持的畸形结构

CFF/OTF（`OTTO`）、WOFF/WOFF2/TTC 报为「不支持」，同样不做半解析。

## 子集导出

导出 TTF 为重新打包的有效 TrueType（sfnt 1.0）：

- glyph 重排为新 gid（0 仍是 `.notdef`），`glyf` 中复合组件 gid 同步重写
- 重建 `cmap`（format 4 / 12 / 14）、`loca`(long)、`hmtx`、`hhea`、
  `maxp`、`head`、`post`(format 3)
- `name` 表**原样复制**，许可证（nameID 13/14 等）完整保留；
  `OS/2`、`cvt `、`fpgm`、`prep` 存在时原样保留
- GSUB 仅重写计划启用且解析支持的 lookup（single/multiple/alternate/
  ligature），不支持的 lookup 丢弃并在计划 `dropped_warnings` 记录

## 页面功能

- 表目录摘要（tag/offset/length/checksum 校验）、名称与许可证元数据
- 多段文本样本输入与特性开关
- 本地 SVG 轮廓预览（不依赖系统渲染，明确标出缺失、`.notdef`、
  被连字吸收与替代关系）
- 字符 → glyph 关系图（字符、VS、复合组件、连字/替代边）
- 候选 glyph 列表（含每个 glyph 的完整追溯来源）
- 计划版本选择、两计划对比、下载子集 TTF 与计划 JSON

## 代码结构

```
glyphscope/
  binary.py    # 全边界检查二进制读取器/写入器
  sfnt.py      # SFNT 表目录
  glyf.py      # head/maxp/hhea/hmtx/loca/glyf，复合防环与传递闭包
  cmap.py      # cmap format 0/4/6/12/14
  layout.py    # GSUB/GPOS 解析（single/multiple/alternate/ligature）
  nametbl.py   # name（许可证）解析
  post.py      # post glyph 名
  planner.py   # 文本聚类、shaping 模拟、候选集与追溯
  subset.py    # 子集 TTF 导出（许可证保留、GSUB 重写）
  gsubbuild.py # 确定性 GSUB/coverage 写入
  builder.py   # SFNT/cmap/基础表写入（夹具与导出共用）
  fixtures.py  # 代码构造的 MIT 演示字体
  storage.py   # 原始字节、隔离区、计划版本
  service.py   # 编排、关系图、SVG 预览、计划对比
  webapp.py    # 零依赖 HTTP 服务
  web/static/  # 前端单页
tests/         # 31 个测试（见下）
```

## 测试覆盖

代理平面字符、variation selector（默认/非默认/基字依赖）、组合字符与
复合传递闭包、复合依赖环、损坏 offset / cmap / sfnt、缺失与 `.notdef` 区分、
确定性 glyph 排序与字节级可重现、计划版本化与对比、`plan_only`、
许可证保留、导出回灌后连字/VS 仍成立、关系图与 SVG、HTTP 冒烟。
