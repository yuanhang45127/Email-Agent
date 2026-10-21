"""Command-line entry point.

    python -m mailradar sync [--full] [--dry-run] [--no-commit]
    python -m mailradar push <draft.md> [more.md ...] [--apply]
    python -m mailradar scan-box
    python -m mailradar notify "标题" "正文" [--channels ntfy,weixin]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .drafts import parse_draft, push_drafts, scan_draft_box_json
from .notify import Notifier
from .sync import run_sync


def _cmd_sync(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    summary = run_sync(cfg, full=args.full, dry_run=args.dry_run, commit=not args.no_commit)
    print(f"done: scanned {summary['scanned']}, archived {summary['archived']}, "
          f"sent-hits {summary['sent_hits']}, reply-hits {summary['reply_hits']}, "
          f"contacts {summary['contacts']}")
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    drafts = [parse_draft(Path(p)) for p in args.files]
    result = push_drafts(cfg, drafts, apply=args.apply, sender_name=args.sender_name)
    print(f"done: pushed {result['pushed']}, skipped {result['skipped']}")
    return 0


def _cmd_scan_box(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    print(scan_draft_box_json(cfg))
    return 0


def _cmd_notify(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    channels = args.channels.split(",") if args.channels else None
    results = Notifier(cfg).send(args.title, args.body, channels=channels)
    for channel, status in results.items():
        print(f"{channel}: {status}")
    return 0 if all(v == "ok" for v in results.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mailradar", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="sync mailbox: archive inbound, write back status")
    p_sync.add_argument("--full", action="store_true", help="full rescan (idempotent)")
    p_sync.add_argument("--dry-run", action="store_true", help="preview only")
    p_sync.add_argument("--no-commit", action="store_true", help="skip auto git commit")
    p_sync.set_defaults(func=_cmd_sync)

    p_push = sub.add_parser("push", help="push local markdown drafts to the IMAP drafts box")
    p_push.add_argument("files", nargs="+", help="draft .md files")
    p_push.add_argument("--apply", action="store_true", help="actually push (default dry-run)")
    p_push.add_argument("--sender-name", default="", help="display name for the From header")
    p_push.set_defaults(func=_cmd_push)

    p_scan = sub.add_parser("scan-box", help="read-only JSON snapshot of the drafts box")
    p_scan.set_defaults(func=_cmd_scan_box)

    p_notify = sub.add_parser("notify", help="send a notification through configured channels")
    p_notify.add_argument("title")
    p_notify.add_argument("body", nargs="?", default="")
    p_notify.add_argument("--channels", help="comma-separated, e.g. ntfy,weixin")
    p_notify.set_defaults(func=_cmd_notify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
