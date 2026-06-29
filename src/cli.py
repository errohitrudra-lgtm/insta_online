"""
CLI for the Instagram Reel Monitor.

Usage:
    python -m src run [--config config.json]
    python -m src status
    python -m src refresh
    python -m src jobs
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_run(args: argparse.Namespace) -> None:
    from .main import main
    main(config_path=args.config, visible=args.visible)


def cmd_status(args: argparse.Namespace) -> None:
    import requests
    url = f"http://localhost:{args.port}/api/status"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        print(json.dumps(resp.json(), indent=2))
    except requests.ConnectionError:
        print(f"Error: Cannot connect to {url}. Is the system running?")
        sys.exit(1)


def cmd_refresh(args: argparse.Namespace) -> None:
    import requests
    url = f"http://localhost:{args.port}/api/refresh"
    try:
        resp = requests.post(url, timeout=5)
        resp.raise_for_status()
        print("Refresh triggered:", resp.json())
    except requests.ConnectionError:
        print(f"Error: Cannot connect to {url}. Is the system running?")
        sys.exit(1)


def cmd_jobs(args: argparse.Namespace) -> None:
    import requests
    url = f"http://localhost:{args.port}/api/jobs"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        jobs = resp.json()
        if not jobs:
            print("No jobs yet.")
            return
        for j in jobs:
            print(
                f"  [{j['status']:>12}]  reel={j['reel_shortcode']}  "
                f"views={j.get('view_count', '?')}  "
                f"from=@{j.get('target_username', '?')}"
            )
    except requests.ConnectionError:
        print(f"Error: Cannot connect to {url}. Is the system running?")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insta-reel-monitor",
        description="Instagram Reel Monitor – Download reels from target accounts",
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Start the monitoring system")
    p_run.add_argument("-c", "--config", default="config.json")
    p_run.add_argument("-v", "--visible", action="store_true",
                       help="Show the browser window (non-headless)")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="Show system status")
    p_status.add_argument("-p", "--port", type=int, default=8000)
    p_status.set_defaults(func=cmd_status)

    p_refresh = sub.add_parser("refresh", help="Trigger immediate refresh")
    p_refresh.add_argument("-p", "--port", type=int, default=8000)
    p_refresh.set_defaults(func=cmd_refresh)

    p_jobs = sub.add_parser("jobs", help="List all jobs")
    p_jobs.add_argument("-p", "--port", type=int, default=8000)
    p_jobs.set_defaults(func=cmd_jobs)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
