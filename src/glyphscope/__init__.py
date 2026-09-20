"""glyphscope —— 字形子集检查站。

模块划分：
  errors   字体损坏 / 隔离异常
  loader   原始字节保存、严格解析、隔离判定
  segments Unicode 文本分段（含代理平面与 variation selector）
  mapper   cmap / format-14 变体映射
  layout   GSUB 布局模拟、特性闭合、替代追踪
  planner  候选 glyph 集、溯源、版本化计划
  exporter 确定性子集导出、许可证元数据保留
  preview  本地 SVG 预览与关系图
  storage  原始文件 / 计划 / 导出的内容寻址存储
  web      Flask 页面
"""

SPEC_VERSION = "1.0"

__all__ = ["SPEC_VERSION"]
