"""把多语言文本切成带溯源信息的码点簇（cluster）。

Python 内部 str 即 Unicode 标量序列，因此代理平面（astral plane）
字符天然为单个码点；模块仍然显式拒绝孤立代理，保证输入边界清晰。
"""

from dataclasses import dataclass, field
from typing import List, Optional

ZWJ = 0x200D
ZWNJ = 0x200C


def is_variation_selector(cp):
    """U+FE00..U+FE0F（VS1..VS16）或 U+E0100..U+E01EF（VS17..VS256）。"""
    # Unicode 变体选择符：VS1..VS16 = U+FE00..U+FE0F；
    # VS17..VS256 = U+E0100..U+E01EF。
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def is_surrogate(cp):
    return 0xD800 <= cp <= 0xDFFF


def is_whitespace(cp):
    """Unicode 空白；这些码点缺字时不算“缺失字符”，只跳过布局。"""
    import unicodedata

    return unicodedata.category(chr(cp)) == "Zs" or cp in (0x09, 0x0A, 0x0D)


def cp_label(cp):
    """码点的紧凑展示标签，如 ``U+1F600``。"""
    return "U+%04X" % cp


@dataclass
class Cluster:
    """一个视觉簇：基字符 + 可选的变体选择符与组合标记。

    ``marks`` 中也可能夹带变体选择符之后的额外 VS（极少见），
    组合标记按文本顺序保留，供布局模拟使用。
    """

    base: int
    base_label: str
    text_index: int
    chars: List[int] = field(default_factory=list)
    variation_selector: Optional[int] = None
    marks: List[int] = field(default_factory=list)

    @property
    def text(self):
        return "".join(chr(c) for c in self.chars)

    def key(self):
        """用于计划去重的稳定键。"""
        return tuple(self.chars)


def segment_text(text):
    """把文本切为 :class:`Cluster` 列表。

    - 孤立代理抛 ``ValueError``（上游应先按 UTF-8 解码，正常上传不会出现）；
    - 变体选择符附加在紧邻的前一个基字符上；
    - ZWJ/ZWNJ 各自成簇，交给 GSUB 布局阶段处理；
    - 其余控制字符与空白各自成簇。
    """
    clusters = []  # type: List[Cluster]
    i = 0
    n = len(text)
    while i < n:
        cp = ord(text[i])
        if is_surrogate(cp):
            raise ValueError("文本包含孤立代理码点 U+%04X" % cp)
        start = i
        chars = [cp]
        vs = None
        marks = []
        i += 1
        if not is_variation_selector(cp) and cp not in (ZWJ, ZWNJ):
            while i < n:
                nxt = ord(text[i])
                if is_surrogate(nxt):
                    raise ValueError("文本包含孤立代理码点 U+%04X" % nxt)
                if vs is None and is_variation_selector(nxt):
                    vs = nxt
                    chars.append(nxt)
                    i += 1
                    continue
                if _is_combining_mark(nxt):
                    marks.append(nxt)
                    chars.append(nxt)
                    i += 1
                    continue
                break
        clusters.append(
            Cluster(
                base=cp,
                base_label=cp_label(cp),
                text_index=start,
                chars=chars,
                variation_selector=vs,
                marks=marks,
            )
        )
    return clusters


def _is_combining_mark(cp):
    """识别需要附着到前一基字符的组合标记。

    使用 Unicode 一般类别 Mn/Mc/Me。Python 自带 unicodedata，
    无需额外数据文件，离线可用。
    """
    import unicodedata

    return unicodedata.category(chr(cp)) in ("Mn", "Mc", "Me")
