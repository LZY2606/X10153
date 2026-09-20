import io

import pytest

from builder import corrupt_directory_offset, fixture_bytes
from glyphscope.web.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "store"))
    app.config["TESTING"] = True
    return app.test_client()


def _upload(client, data, name="fixture.ttf"):
    return client.post(
        "/upload",
        data={"font": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_index_title(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "字形子集检查站".encode("utf-8") in resp.data


def test_full_web_flow(client):
    resp = _upload(client, fixture_bytes())
    assert resp.status_code == 200
    assert "字体表摘要".encode("utf-8") in resp.data

    # 字体列表页可访问。
    home = client.get("/").data.decode("utf-8")
    assert "fixture.ttf" in home

    # 建计划（仅计划）。
    resp = client.post(
        "/plans/new",
        data={
            "font_sha256": _sha(client),
            "samples": "fi a\U0001F600",
            "features": "liga",
            "closure": "reachable",
            "action": "plan",
        },
        follow_redirects=True,
    )
    assert "候选 glyph".encode("utf-8") in resp.data
    plan_id = resp.request.path.strip("/").split("/")[-1]

    # 关系图页面。
    graph = client.get("/plans/%s/graph" % plan_id)
    assert graph.status_code == 200
    assert "关系图".encode("utf-8") in graph.data

    # 计划 JSON 可下载。
    payload = client.get("/plans/%s/json" % plan_id)
    assert payload.status_code == 200
    assert payload.is_json

    # 导出子集并下载。
    client.post("/plans/%s/export" % plan_id, follow_redirects=True)
    subset = client.get("/plans/%s/download" % plan_id)
    assert subset.status_code == 200
    assert subset.data[:4] in (b"\x00\x01\x00\x00", b"OTTO")


def test_quarantined_font_page_blocks_plan(client):
    resp = _upload(client, corrupt_directory_offset(fixture_bytes()), "bad.ttf")
    body = resp.data.decode("utf-8")
    assert "已隔离" in body
    # 新建计划表单不提供隔离字体（字体列表接口也标记 quarantined）。
    new_page = client.get("/plans/new").data.decode("utf-8")
    assert "bad.ttf" not in new_page


def test_compare_page(client):
    _upload(client, fixture_bytes())
    sha = _sha(client)
    ids = []
    for closure in ("none", "full"):
        resp = client.post(
            "/plans/new",
            data={
                "font_sha256": sha,
                "samples": "fi",
                "features": "liga",
                "closure": closure,
                "action": "plan",
            },
            follow_redirects=True,
        )
        ids.append(resp.request.path.strip("/").split("/")[-1])
    resp = client.post(
        "/compare",
        data={"plan_a": ids[0], "plan_b": ids[1]},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "仅 B 包含".encode("utf-8") in resp.data


def _sha(client):
    from glyphscope.web.app import create_app  # noqa: F401

    # 从首页链接里拿 sha 最稳妥。
    body = client.get("/").data.decode("utf-8")
    import re

    match = re.search(r"/fonts/([0-9a-f]{64})", body)
    assert match, body[:200]
    return match.group(1)
