from pathlib import Path

SRC = sorted((Path(__file__).resolve().parents[2] / "bridge").glob("*.py"))


def _text():
    return {p.name: p.read_text() for p in SRC}


def test_sources_found():
    assert {"server.py", "store.py", "__main__.py"} <= set(_text())


def test_no_network_imports():
    bad = ["import socket", "urllib", "http.", "import http", "requests", "asyncio", "ssl", "xmlrpc"]
    for name, text in _text().items():
        for b in bad:
            assert b not in text, f"{name} contains {b!r}"


def test_never_creates_key_or_db():
    for name, text in _text().items():
        for b in ["get_or_create_key", "init_schema", ".set("]:
            assert b not in text, f"{name} contains {b!r}"


def test_no_input_hooks():
    bad = ["CGEventTap", "addGlobalMonitorForEvents", "addLocalMonitorForEvents", "IOHIDManager",
           "CGRequestListenEventAccess"]
    for name, text in _text().items():
        for b in bad:
            assert b not in text, f"{name} contains {b!r}"
