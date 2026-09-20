import os
import tempfile

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GLYPHSCOPE_HOME", str(tmp_path / "home"))
    return str(tmp_path / "home")


@pytest.fixture()
def service(home):
    from glyphscope.service import Service
    return Service()


@pytest.fixture()
def demo_font():
    from glyphscope.fixtures import make_demo_font
    return make_demo_font()
