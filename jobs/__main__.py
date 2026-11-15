"""uv run python -m jobs {deploy,status,uninstall}: the Python background jobs (see jobs/deploy.py)."""

from __future__ import annotations

import argparse

from jobs import deploy as d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobs", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dp = sub.add_parser("deploy", help="copy --ref to the runtime dir and restart the jobs")
    dp.add_argument("--ref", default="main", help="git ref to deploy (committed tree only)")
    g = dp.add_mutually_exclusive_group()
    g.add_argument("--prompts", dest="prompts", action="store_true", default=None,
                   help="also run the check-in scheduler and nudge engine")
    g.add_argument("--no-prompts", dest="prompts", action="store_false", help="turn prompts off")
    sub.add_parser("status", help="deployed commit and running jobs")
    sub.add_parser("uninstall", help="stop the jobs (removes jobs.json) and any old LaunchAgents")
    args = ap.parse_args(argv)
    if args.cmd == "deploy":
        d.deploy(args.ref, args.prompts)
        return 0
    if args.cmd == "status":
        return d.status()
    d.uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
