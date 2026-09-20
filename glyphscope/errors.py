"""异常类型。

字体损坏（结构非法、越界 offset、循环组件）一律抛 :class:`FontCorrupt`，
调用方据此把整份字体放入隔离区，绝不带着半棵依赖树继续。
"""


class GlyphScopeError(Exception):
    """glyphscope 基础异常。"""


class FontCorrupt(GlyphScopeError):
    """字体表结构损坏：越界 offset、非法字段、循环组件等。

    该错误意味着字体不可信，整份字体进入隔离区。
    """

    def __init__(self, reason, table=None, detail=None):
        self.reason = reason
        self.table = table
        self.detail = detail
        message = reason
        if table:
            message = "%s (table=%s)" % (message, table)
        if detail:
            message = "%s: %s" % (message, detail)
        super().__init__(message)


class UnsupportedFont(GlyphScopeError):
    """结构合法但 glyphscope 不支持的字体类型（例如 CFF 轮廓）。"""
