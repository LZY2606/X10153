import json
import urllib.request

import pytest

from glyphscope.fixtures import make_demo_font
from glyphscope.webapp import make_server


@pytest.fixture()
def server(home):
    from glyphscope.service import Service
    httpd = make_server("127.0.0.1", 0, Service())
    port = httpd.server_address[1]
    import threading
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % port
    httpd.shutdown()


def post_json(url, obj):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url):
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_index_title(server):
    with urllib.request.urlopen(server + "/") as resp:
        html = resp.read().decode("utf-8")
    assert "字形子集检查站" in html


def test_upload_plan_download_flow(server):
    raw = make_demo_font()
    boundary = "----testboundary"
    body = (
        "--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="font"; '
        'filename="demo.ttf"\r\n'
        "Content-Type: font/ttf\r\n\r\n"
    ).encode("utf-8") + raw + ("\r\n--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        server + "/api/fonts", data=body,
        headers={"Content-Type":
                 "multipart/form-data; boundary=" + boundary},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        up = json.loads(resp.read().decode("utf-8"))
    assert up["status"] == "ok"
    digest = up["digest"]

    plan = post_json(server + "/api/fonts/" + digest + "/plans", {
        "samples": [{"text": "fiA\ufe00", "label": "s"}],
        "features": ["liga", "ss01"],
        "feature_policy": "closure",
    })
    assert plan["version"] >= 1
    v = plan["version"]
    with urllib.request.urlopen(
            server + "/api/fonts/%s/plans/%d/download" % (digest, v)) as r:
        subset = r.read()
    assert subset[:4] == b"\x00\x01\x00\x00"

    graph = get_json(
        server + "/api/fonts/%s/plans/%d/graph" % (digest, v))
    assert graph["nodes"] and graph["links"]


def test_corrupt_upload_quarantined(server):
    req = urllib.request.Request(
        server + "/api/fonts", data=b"\x00\x01\x00\x00broken",
        headers={"Content-Type": "application/octet-stream"},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        up = json.loads(resp.read().decode("utf-8"))
    assert up["status"] in ("quarantined", "unsupported")
