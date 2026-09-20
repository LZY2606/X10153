"""字体解析阶段的损坏 / 隔离错误。

任何 :class:`FontCorruptError` 都意味着整份字体必须进入隔离，
调用方不允许继续使用半截的依赖树。
"""


class GlyphScopeError(Exception):
    """glyphscope 基础异常。"""


class FontCorruptError(GlyphScopeError):
    """字体表损坏、越界 offset 或循环组件等不可恢复问题。

    ``table`` 为触发问题的表标签（cmap/glyf/GSUB ...），
    未知阶段使用 ``"sfnt"``。
    """

    def __init__(self, message, table="sfnt"):
        super().__init__("[%s] %s" % (table, message))
        self.table = table


class QuarantinedFontError(GlyphScopeError):
    """试图对已隔离字体执行分析或导出。"""


class PlanError(GlyphScopeError):
    """计划参数非法或计划不存在。"""
