"""Local web control panel for MailRadar.

Serves a single-page dashboard and a small JSON API that wraps the engine:
view contacts, run sync, inspect the drafts box, push a draft, send a
notification. Binds to 127.0.0.1 by default — it is a local tool, not a
public service.

Run with:  python -m mailradar panel [--port 8790]
"""

from __future__ import annotations

import io
import json
import sys
import threading
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import Config, load_config
from .contacts import load_contacts
from .drafts import scan_draft_box, push_drafts, Draft
from .notify import Notifier
from .sync import run_sync

INDEX = Path(__file__).resolve().parent / "panel_index.html"

# In-memory background job store for long-running sync.
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_JOB_SEQ = 0


def _next_job_id() -> str:
    global _JOB_SEQ
    with _JOBS_LOCK:
        _JOB_SEQ += 1
        return f"job{_JOB_SEQ}"


def _contacts_payload(cfg: Config) -> list[dict]:
    book = load_contacts(cfg)
    name_col = cfg.contacts.columns.get("name", "name")
    org_col = cfg.contacts.columns.get("org", "org")
    status_col = cfg.contacts.columns.get("status", "status")
    sent_col = cfg.contacts.columns.get("sent", "sent")
    replied_col = cfg.contacts.columns.get("replied", "replied")
    out = []
    for contact in book.by_email.values():
        row = contact.row
        out.append({
            "name": row.get(name_col, ""),
            "email": row.get("__email__", ""),
            "org": row.get(org_col, ""),
            "status": row.get(status_col, ""),
            "sent": (row.get(sent_col) or "").strip() == cfg.contacts.truthy,
            "replied": (row.get(replied_col) or "").strip() == cfg.contacts.truthy,
        })
    # De-duplicate (a contact may back several addresses) and sort by name.
    seen, uniq = set(), []
    for c in sorted(out, key=lambda x: x["name"]):
        key = (c["name"], c["email"])
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _state_payload(cfg: Config) -> dict:
    import datetime
    state_path = cfg.resolve(cfg.archive.state_file)
    last = None
    if state_path.is_file():
        last = datetime.datetime.fromtimestamp(state_path.stat().st_mtime).strftime("%m-%d %H:%M")
    return {"last_run": last, "state_file": state_path.name}


def _run_sync_job(job_id: str, cfg: Config, dry_run: bool) -> None:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            summary = run_sync(cfg, dry_run=dry_run, commit=not dry_run)
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="done", result=summary)
    except Exception as exc:  # noqa: BLE001 - surface to the UI
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=str(exc))
    finally:
        with _JOBS_LOCK:
            _JOBS[job_id]["output"] = buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "MailRadarPanel/0.1"

    # -- helpers ---------------------------------------------------------
    def _cfg(self) -> Config:
        return self.server.cfg  # type: ignore[attr-defined]

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, *args):  # quiet the default stderr logging
        pass

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                self._serve_index()
            elif path == "/api/config":
                self._api_config()
            elif path == "/api/contacts":
                self._send_json(_contacts_payload(self._cfg()))
            elif path == "/api/draft-box":
                self._send_json(scan_draft_box(self._cfg()))
            elif path == "/api/state":
                self._send_json(_state_payload(self._cfg()))
            elif path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                with _JOBS_LOCK:
                    job = _JOBS.get(job_id)
                self._send_json(job or {"error": "unknown job"})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_json()
        try:
            if path == "/api/sync":
                self._api_sync(body)
            elif path == "/api/push":
                self._api_push(body)
            elif path == "/api/notify":
                self._api_notify(body)
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, 500)

    # -- handlers --------------------------------------------------------
    def _serve_index(self) -> None:
        html = INDEX.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _api_config(self) -> None:
        cfg = self._cfg()
        self._send_json({
            "imap_user": cfg.imap.user,
            "channels": cfg.notify.channels,
            "weixin_enabled": cfg.notify.weixin.enabled,
            "contacts_sources": cfg.contacts.sources,
        })

    def _api_sync(self, body: dict) -> None:
        dry_run = bool(body.get("dry_run", True))
        job_id = _next_job_id()
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "running", "output": ""}
        threading.Thread(target=_run_sync_job, args=(job_id, self._cfg(), dry_run), daemon=True).start()
        self._send_json({"job_id": job_id})

    def _api_push(self, body: dict) -> None:
        subject = (body.get("subject") or "").strip()
        to = (body.get("to") or "").strip()
        text = body.get("body") or ""
        if not (subject and to and text):
            self._send_json({"error": "subject/to/body required"}, 400)
            return
        draft = Draft(subject=subject, to=to, body=text, source=Path("<panel>"))
        result = push_drafts(self._cfg(), [draft], apply=True)
        self._send_json(result)

    def _api_notify(self, body: dict) -> None:
        title = (body.get("title") or "").strip()
        text = body.get("body") or ""
        channels = body.get("channels") or None
        if not title:
            self._send_json({"error": "title required"}, 400)
            return
        self._send_json(Notifier(self._cfg()).send(title, text, channels=channels))


def serve(config_path: str | None, host: str, port: int) -> None:
    try:
        cfg = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot load config: {exc}", file=sys.stderr)
        print("hint: copy config.example.yaml to config.yaml and set .env", file=sys.stderr)
        raise SystemExit(2)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.cfg = cfg  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"MailRadar panel -> {url}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
