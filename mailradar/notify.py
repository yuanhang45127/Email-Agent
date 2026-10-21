"""Notification fan-out.

Two delivery backends are supported and can be combined:

* **ntfy** — POST to a self-hosted ntfy topic (simple, reliable, phone push).
* **weixin** — deliver through an OpenClaw gateway's WeChat channel
  (``openclaw agent --channel openclaw-weixin --to <user> --deliver``), which
  turns a notification into a message in your WeChat chat.

Both are best-effort: a failing channel is reported, never raised, so a
notification problem never breaks the sync/radar pipeline that triggered it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config


class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def send(self, title: str, body: str = "", *, channels: list[str] | None = None) -> dict[str, str]:
        channels = channels or self.cfg.notify.channels
        results: dict[str, str] = {}
        for channel in channels:
            handler = getattr(self, f"_send_{channel}", None)
            if handler is None:
                results[channel] = f"unknown channel: {channel}"
                continue
            try:
                handler(title, body)
                results[channel] = "ok"
            except Exception as exc:  # best-effort: never propagate
                results[channel] = f"error: {exc}"
        return results

    # -- ntfy ------------------------------------------------------------
    def _send_ntfy(self, title: str, body: str) -> None:
        ntfy = self.cfg.notify.ntfy
        if not ntfy.topic:
            raise ValueError("notify.ntfy.topic is not configured")
        cmd = ["curl", "-sS", "-m", "30", "-H", f"Title: {title}", "-d", body, ntfy.topic]
        if ntfy.auth_file and Path(ntfy.auth_file).is_file():
            creds = Path(ntfy.auth_file).read_text(encoding="utf-8").strip()
            cmd[3:3] = ["-u", creds]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr.strip()}")

    # -- weixin (OpenClaw gateway) --------------------------------------
    def _send_weixin(self, title: str, body: str) -> None:
        wx = self.cfg.notify.weixin
        if not wx.enabled:
            raise ValueError("notify.weixin.enabled is false")
        if not wx.to:
            raise ValueError("notify.weixin.to is not configured")
        message = f"{title}\n{body}".strip()
        cmd = [
            "openclaw", "agent",
            "-m", message,
            "--channel", wx.channel,
            "--to", wx.to,
            "--deliver",
            "--thinking", "off",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"openclaw agent failed: {result.stderr.strip()[:200]}")
