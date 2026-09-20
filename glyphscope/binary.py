"""最小化、全部带边界检查的二进制读取器。

任何越界读取都抛 :class:`FontCorrupt`，上层保证整份字体进入隔离。
"""
import struct

from .errors import FontCorrupt


class Reader:
    __slots__ = ("data", "pos", "limit", "start", "table")

    def __init__(self, data, start=0, limit=None, table=None):
        self.data = data
        self.start = start
        self.pos = start
        self.limit = len(data) if limit is None else limit
        self.table = table

    def remaining(self):
        return self.limit - self.pos

    def require(self, n):
        if n < 0 or self.pos + n > self.limit:
            raise FontCorrupt(
                "越界读取", self.table,
                "需要 %d 字节, 偏移 %d, 表长 %d" % (n, self.pos - self.start,
                                                  self.limit - self.start))

    def u8(self):
        self.require(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i8(self):
        self.require(1)
        v = self.data[self.pos]
        self.pos += 1
        return v - 256 if v > 127 else v

    def u16(self):
        self.require(2)
        v = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i16(self):
        self.require(2)
        v = struct.unpack_from(">h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u24(self):
        self.require(3)
        d = self.data
        v = (d[self.pos] << 16) | (d[self.pos + 1] << 8) | d[self.pos + 2]
        self.pos += 3
        return v

    def u32(self):
        self.require(4)
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def tag(self):
        self.require(4)
        v = self.data[self.pos:self.pos + 4]
        self.pos += 4
        try:
            return v.decode("latin-1")
        except UnicodeDecodeError:
            raise FontCorrupt("非法表标签", self.table)

    def fixed(self):
        return self.u32() / 65536.0

    def bytes(self, n):
        self.require(n)
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return bytes(v)

    def skip(self, n):
        self.require(n)
        self.pos += n

    def seek(self, pos):
        if pos < self.start or pos > self.limit:
            raise FontCorrupt(
                "越界跳转", self.table,
                "偏移 %d 超出 [%d, %d]" % (pos, self.start, self.limit))
        self.pos = pos

    def sub(self, rel, length=None):
        """从相对当前表起点的偏移建立子读取器，并检查长度。"""
        if rel < 0 or self.start + rel > self.limit:
            raise FontCorrupt(
                "非法子表偏移", self.table,
                "rel=%d table_len=%d" % (rel, self.limit - self.start))
        limit = self.limit if length is None else self.start + rel + length
        if limit > self.limit:
            raise FontCorrupt("子表长度越界", self.table)
        return Reader(self.data, self.start + rel, limit, self.table)

    def uint(self, fmt_size):
        if fmt_size == 2:
            return self.u16()
        if fmt_size == 4:
            return self.u32()
        raise FontCorrupt("非法 OffsetSize", self.table, str(fmt_size))


class Writer:
    """大端字节写入器。"""

    def __init__(self):
        self.parts = []

    def u8(self, v):
        self.parts.append(struct.pack(">B", v & 0xFF))

    def i8(self, v):
        self.parts.append(struct.pack(">b", v))

    def u16(self, v):
        self.parts.append(struct.pack(">H", v & 0xFFFF))

    def i16(self, v):
        self.parts.append(struct.pack(">h", v))

    def u24(self, v):
        self.parts.append(struct.pack(">I", v & 0xFFFFFF)[1:])

    def u32(self, v):
        self.parts.append(struct.pack(">I", v & 0xFFFFFFFF))

    def tag(self, s):
        self.parts.append(s.encode("latin-1"))

    def raw(self, b):
        self.parts.append(bytes(b))

    def pad4(self):
        used = sum(len(p) for p in self.parts)
        if used % 4:
            self.parts.append(b"\x00" * (4 - used % 4))

    def bytes(self):
        return b"".join(self.parts)

    def length(self):
        return sum(len(p) for p in self.parts)


def tag_to_int(tag):
    return struct.unpack(">I", tag.encode("latin-1"))[0]
