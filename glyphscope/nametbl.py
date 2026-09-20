"""name 表解析：重点保留许可证（nameID 13/14）等元数据。

name 表损坏不影响 glyph 结构，按宽松策略处理：能读多少读多少，
并在 warnings 中记录；不会因此隔离整份字体。
"""
from __future__ import annotations

from .binary import Reader
from .errors import FontCorrupt

NAME_FAMILY = 1
NAME_SUBFAMILY = 2
NAME_UNIQUE = 3
NAME_FULL = 4
NAME_VERSION = 5
NAME_PS = 6
NAME_TRADEMARK = 7
NAME_MANUFACTURER = 8
NAME_DESIGNER = 9
NAME_DESCRIPTION = 10
NAME_VENDOR_URL = 11
NAME_DESIGNER_URL = 12
NAME_LICENSE = 13
NAME_LICENSE_URL = 14
NAME_TYPO_FAMILY = 16


def parse_name(r):
    records = []
    warnings = []
    try:
        fmt = r.u16()
        count = r.u16()
        string_offset = r.u16()
        if fmt not in (0, 1):
            raise FontCorrupt("name format 非法", "name", str(fmt))
        if string_offset < 6 + 12 * count or string_offset > r.limit - r.start:
            raise FontCorrupt("name stringOffset 越界", "name")
        rows = []
        for i in range(count):
            platform_id = r.u16()
            encoding_id = r.u16()
            language_id = r.u16()
            name_id = r.u16()
            length = r.u16()
            offset = r.u16()
            rows.append((platform_id, encoding_id, language_id, name_id,
                         length, offset))
        strings_start = string_offset
        for platform_id, encoding_id, language_id, name_id, length, offset \
                in rows:
            abs_off = strings_start + offset
            if abs_off + length > r.limit - r.start:
                warnings.append("nameID %d 字符串越界，已跳过" % name_id)
                continue
            raw = r.data[r.start + abs_off:r.start + abs_off + length]
            text = _decode(raw, platform_id, encoding_id)
            if text is not None:
                records.append((name_id, platform_id, language_id, text))
    except FontCorrupt as exc:
        warnings.append(str(exc))

    # 优先选择 Windows/Unicode 英文记录，再退而求其次
    by_id = {}
    for name_id, platform_id, language_id, text in records:
        score = _score(platform_id, language_id)
        prev = by_id.get(name_id)
        if prev is None or score > prev[0]:
            by_id[name_id] = (score, platform_id, language_id, text)
    names = {nid: v[3] for nid, v in by_id.items()}
    return {
        "records": [
            {"name_id": nid, "platform_id": pid, "language_id": lid,
             "text": text}
            for nid, pid, lid, text in records
        ],
        "names": names,
        "warnings": warnings,
    }


def _score(platform_id, language_id):
    score = 0
    if platform_id == 3:
        score += 10
    elif platform_id == 0:
        score += 5
    if language_id in (0, 0x0409):
        score += 2
    return score


def _decode(raw, platform_id, encoding_id):
    if not raw:
        return ""
    if platform_id == 3 or platform_id == 0:
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            try:
                return raw.decode("utf-16-be", errors="replace")
            except Exception:
                return None
    if platform_id == 1:
        try:
            return raw.decode("mac_roman")
        except UnicodeDecodeError:
            return raw.decode("mac_roman", errors="replace")
    try:
        return raw.decode("latin-1")
    except UnicodeDecodeError:
        return None
