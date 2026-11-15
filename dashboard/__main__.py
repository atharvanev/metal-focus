"""uv run python -m dashboard [--port N] [--no-open]: local activity dashboard at 127.0.0.1."""

from __future__ import annotations

import argparse
import webbrowser

from dashboard.server import DashboardServer
from data.db import open_existing


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dashboard", description=__doc__)
    ap.add_argument("--port", type=int, default=8737, help="local port (0 = any free port)")
    ap.add_argument("--no-open", action="store_true", help="print the URL instead of opening a browser")
    args = ap.parse_args(argv)
    srv = DashboardServer(args.port, open_existing)
    print(f"Metal Focus dashboard: {srv.url}\n(local only; Ctrl-C to stop)", flush=True)
    if not args.no_open:
        webbrowser.open(srv.url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
