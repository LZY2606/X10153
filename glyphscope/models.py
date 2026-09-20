"""核心数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .postnames import MAC_GLYPH_NAMES


@dataclass(frozen=True)
class CompositePart:
    glyph_index: int
    flags: int
    argument1: int
    argument2: int
    transform: Tuple[float, float, float, float, float, float]


@dataclass
class GlyphInfo:
    index: int
    name: str
    is_composite: bool = False
    components: List[CompositePart] = field(default_factory=list)
    number_of_contours: int = 0
    advance: int = 0
    x_min: int = 0
    y_min: int = 0
    x_max: int = 0
    y_max: int = 0
    has_outline: bool = True


@dataclass(frozen=True)
class VariationRecord:
    unicode_value_selectors: int
    default_ranges: Optional[List[Tuple[int, int]]]  # 默认 UVS：glyph 由 base cmap 决定
    non_default_mappings: Optional[List[Tuple[int, int]]]  # (base_cp, gid)


@dataclass
class Lookup:
    index: int
    type: int
    type_name: str
    flags: int
    subtables: List[Any]
    coverage: List[int] = field(default_factory=list)
    supported: bool = True
    dropped_reason: Optional[str] = None


@dataclass
class Feature:
    tag: str
    lookup_indices: List[int]
    scripts: List[str] = field(default_factory=list)
    default_on: bool = False


@dataclass
class NameRecord:
    name_id: int
    platform_id: int
    language_id: int
    text: str


@dataclass
class FontMeta:
    sfnt_version: str
    scaler_type: str
    num_glyphs: int
    units_per_em: int
    table_digest: str
    table_summaries: List[Dict[str, Any]]
    names: Dict[int, str]
    license_text: Optional[str]
    license_url: Optional[str]
    family: Optional[str]
    features: List[Dict[str, Any]]
    gpos_features: List[str]
    is_quadratic: bool


def glyph_name_for(index, post_names=None, num_glyphs=0):
    if index == 0:
        return ".notdef"
    if post_names is not None and 0 <= index < len(post_names):
        if post_names[index]:
            return post_names[index]
        return "glyph%d" % index
    return "glyph%d" % index


def plan_to_jsonable(plan):
    return asdict(plan)
