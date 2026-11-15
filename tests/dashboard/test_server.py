import http.client
import json
import threading

import pytest

from dashboard.server import DashboardServer
from data.db import connect
from tests.dashboard.conftest import KEY


@pytest.fixture
def server(db_path):
    srv = DashboardServer(0, lambda: connect(db_path, KEY), token="tok")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def get(srv, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    c.request("GET", path, headers={"Host": host or f"127.0.0.1:{srv.port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def test_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"
    assert server.url == f"http://127.0.0.1:{server.port}/?t=tok"


def test_static_page_with_security_headers(server):
    r, body = get(server, "/")
    assert r.status == 200 and b"<title>Metal Focus</title>" in body
    assert "default-src 'self'" in r.getheader("Content-Security-Policy")
    assert r.getheader("Cache-Control") == "no-store"
    assert get(server, "/app.js")[0].status == 200


def test_api_requires_token(server):
    assert get(server, "/api/days")[0].status == 403
    assert get(server, "/api/days?t=wrong")[0].status == 403
    r, body = get(server, "/api/days?t=tok")
    assert r.status == 200 and json.loads(body)["days"] == ["2026-11-13"]


def test_rejects_foreign_host(server):
    assert get(server, "/api/days?t=tok", host=f"evil.example:{server.port}")[0].status == 403
    assert get(server, "/", host="evil.example")[0].status == 403
    assert get(server, "/api/days?t=tok", host=f"localhost:{server.port}")[0].status == 200


def test_day_and_live(server):
    r, body = get(server, "/api/day?t=tok&d=2026-11-13")
    assert r.status == 200 and json.loads(body)["windows"]
    assert get(server, "/api/day?t=tok")[0].status == 400
    r, body = get(server, "/api/live?t=tok")
    assert r.status == 200 and "running" in json.loads(body)


def test_no_static_path_traversal(server):
    assert get(server, "/../views.py")[0].status == 404
    assert get(server, "/static/../server.py")[0].status == 404


def test_db_error_is_503(db_path):
    def boom():
        raise FileNotFoundError("no db")

    srv = DashboardServer(0, boom, token="tok")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        r, body = get(srv, "/api/days?t=tok")
        assert r.status == 503 and "no db" in json.loads(body)["error"]
    finally:
        srv.shutdown()
        srv.server_close()
