"""post 表解析（format 1/2/3/4，format4 无 glyph 名，忽略）。"""
from __future__ import annotations

from .binary import Reader
from .errors import FontCorrupt

from .postnames import MAC_GLYPH_NAMES


def parse_post(r, num_glyphs):
    version = r.u32()
    if version not in (0x00010000, 0x00020000, 0x00030000, 0x00040000):
        raise FontCorrupt("非法 post.version", "post", hex(version))
    names = None
    if version == 0x00010000:
        if len(MAC_GLYPH_NAMES) < num_glyphs:
            names = list(MAC_GLYPH_NAMES) + [""] * (
                num_glyphs - len(MAC_GLYPH_NAMES))
        else:
            names = list(MAC_GLYPH_NAMES[:num_glyphs])
    elif version == 0x00020000:
        r.skip(28)  # italicAngle..minMemType42 等固定头
        count = r.u16()
        if count != num_glyphs:
            raise FontCorrupt("post format2 glyphNameIndex 数与 maxp 不符",
                              "post", "%d!=%d" % (count, num_glyphs))
        indices = [r.u16() for _ in range(count)]
        names = [""] * count
        # 先读全部 Pascal 字符串，数量取决于超界 index
        max_index = max(indices) if indices else 0
        extra_needed = max(258, max_index + 1) - 258
        string_table = {}
        if extra_needed > 0:
            for k in range(extra_needed):
                length = r.u8()
                if length == 0:
                    raise FontCorrupt("post 字符串长度为 0", "post")
                data = r.bytes(length)
                try:
                    string_table[258 + k] = data.decode("latin-1")
                except UnicodeDecodeError:
                    raise FontCorrupt("post glyph 名非法", "post")
        for gid, index in enumerate(indices):
            if index < 258:
                names[gid] = MAC_GLYPH_NAMES[index]
            elif index in string_table:
                names[gid] = string_table[index]
            else:
                raise FontCorrupt("post glyph 名索引越界", "post",
                                  "gid=%d index=%d" % (gid, index))
    if r.remaining() and version == 0x00020000:
        # 允许忽略（下划线表等扩展极少见）
        pass
    return {"version": version, "names": names}
