import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from builder import (  # noqa: E402
    LICENSE_COPYRIGHT,
    LICENSE_TEXT,
    LICENSE_URL,
    fixture_bytes,
)
from glyphscope.service import GlyphScopeService  # noqa: E402


@pytest.fixture()
def font_bytes():
    return fixture_bytes()


@pytest.fixture()
def service(tmp_path):
    return GlyphScopeService(str(tmp_path / "store"))


@pytest.fixture()
def uploaded(service, font_bytes):
    return service.upload_font(font_bytes, "fixture.ttf")
