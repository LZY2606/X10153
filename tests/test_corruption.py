import struct

import pytest

from glyphscope.builder import pack_sfnt
from glyphscope.errors import FontCorrupt
from glyphscope.fixtures import make_demo_font
from glyphscope.font import LoadedFont
from glyphscope.sfnt import parse_sfnt


def test_composite_cycle_quarantines():
    raw = make_demo_font(with_cycle=True)
    with pytest.raises(FontCorrupt) as exc:
        LoadedFont(raw)
    assert "环" in str(exc.value)


def _raw_tables(raw):
    sfnt = parse_sfnt(raw)
    return {tag: rec.data for tag, rec in sfnt.tables.items()}


def test_out_of_bounds_table_offset_is_corrupt():
    raw = make_demo_font()
    sfnt = parse_sfnt(raw)
    num = struct.unpack_from(">H", raw, 4)[0]
    damaged = bytearray(raw)
    # 把第一个表记录的 offset 改成文件末尾之外
    struct.pack_into(">I", damaged, 12 + 8, len(raw) + 100)
    with pytest.raises(FontCorrupt):
        LoadedFont(bytes(damaged))


def test_bad_cmap_subtable_offset_is_corrupt():
    raw = bytearray(make_demo_font())
    sfnt = parse_sfnt(bytes(raw))
    cmap_rec = sfnt.tables["cmap"]
    # 第一个 encoding record 的 offset 在 cmap 内偏移 8
    pos = cmap_rec.offset + 8
    struct.pack_into(">I", raw, pos, 0xFFFF)
    with pytest.raises(FontCorrupt):
        LoadedFont(bytes(raw))


def test_corrupt_font_quarantined_as_whole(service, demo_font):
    import os
    sfnt = parse_sfnt(demo_font)
    damaged = bytearray(demo_font)
    cmap_rec = sfnt.tables["cmap"]
    struct.pack_into(">I", damaged, cmap_rec.offset + 8, 0xFFFF)
    result = service.upload(bytes(damaged), "bad.ttf")
    assert result["status"] == "quarantined"
    assert result["quarantine"]["table"] == "cmap"
    # 原始损坏字节被保存
    assert os.path.isfile(os.path.join(
        service.storage.quarantine_dir, result["digest"] + ".bin"))
    # 不能通过 get_font 正常加载
    with pytest.raises(FontCorrupt):
        service.get_font(result["digest"])


def test_bad_sfnt_magic():
    with pytest.raises(FontCorrupt):
        LoadedFont(b"XXXX" + b"\x00" * 100)


def test_empty_or_short_file():
    with pytest.raises(FontCorrupt):
        LoadedFont(b"ab")


def test_loca_offset_beyond_glyf_is_corrupt():
    import struct
    raw = bytearray(make_demo_font())
    sfnt = parse_sfnt(bytes(raw))
    loca = sfnt.tables["loca"]
    glyf = sfnt.tables["glyf"]
    struct.pack_into(">I", raw, loca.offset + len(loca.data) - 4,
                     len(glyf.data) + 50)
    with pytest.raises(FontCorrupt):
        LoadedFont(bytes(raw))
