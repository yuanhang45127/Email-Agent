"""Draft lifecycle: parse local markdown drafts, push them to the IMAP
drafts mailbox, and scan what is currently sitting in the box.

A draft file is a small markdown document::

    **主题**：Re: hello
    **收件人**：someone@example.com
    ```
    body text
    ```

Pushing is append-only and de-duplicated by (subject, recipient) so it is safe
to run repeatedly.
"""

from __future__ import annotations

import json
import re
import time
import imaplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from .config import Config
from .imap_client import ImapClient, decode_header_value

_SUBJECT_RE = re.compile(r"\*\*主题\*\*[：:]\s*(.+)")
_TO_RE = re.compile(r"\*\*收件人\*\*[：:]\s*([\w.+-]+@[\w.-]+)")
_BODY_RE = re.compile(r"```\n(.*?)```", re.S)
# Chinese salutation fallback for legacy drafts without a To header.
_NAME_RE = re.compile(r"尊敬的\s*([一-龥·]{2,4}?)\s*(?:老师|教授|研究员|女士|先生)")


@dataclass
class Draft:
    subject: str
    to: str
    body: str
    source: Path


def parse_draft(path: Path) -> Draft:
    text = path.read_text(encoding="utf-8")
    subject = _first_group(_SUBJECT_RE, text)
    to = _first_group(_TO_RE, text)
    body_m = _BODY_RE.search(text)
    body = body_m.group(1).strip("\n") if body_m else None
    if not (subject and to and body):
        raise ValueError(f"draft missing subject/recipient/body: {path}")
    return Draft(subject=subject.strip(), to=to.strip(), body=body, source=path)


def _first_group(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _existing_keys(conn) -> set[tuple[str, str]]:
    """(subject, recipient) pairs already in the selected drafts mailbox."""
    keys: set[tuple[str, str]] = set()
    typ, data = conn.uid("search", None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return keys
    typ, fetched = conn.uid("fetch", data[0].decode().replace(" ", ","),
                            "(BODY.PEEK[HEADER.FIELDS (SUBJECT TO)])")
    if typ != "OK":
        return keys
    for item in fetched:
        if not (isinstance(item, tuple) and len(item) >= 2):
            continue
        header = item[1].decode("utf-8", errors="replace")
        subj_m = re.search(r"(?im)^subject:\s*(.+)", header)
        subj = decode_header_value(subj_m.group(1)) if subj_m else ""
        to_m = re.search(r"(?im)^to:\s*(.+)", header)
        to = to_m.group(1) if to_m else ""
        addr = re.search(r"[\w.+-]+@[\w.-]+", to)
        keys.add((subj.strip(), addr.group(0).lower() if addr else ""))
    return keys


def push_drafts(cfg: Config, drafts: list[Draft], *, apply: bool,
                sender_name: str = "") -> dict:
    """Append drafts to the IMAP drafts mailbox. ``apply=False`` is a dry run."""
    pushed = skipped = 0
    with ImapClient(cfg.imap) as client:
        box = ImapClient.find_folder(client.conn, "\\Drafts", "草稿")
        client.conn.select(f'"{box}"' if " " in box else box)
        existing = _existing_keys(client.conn)
        print(f"drafts mailbox: {box} ({len(existing)} existing)", flush=True)

        for draft in drafts:
            if (draft.subject, draft.to.lower()) in existing:
                print(f"[skip] already present: {draft.subject} -> {draft.to}", flush=True)
                skipped += 1
                continue
            if not apply:
                print(f"[dry-run] would push: {draft.subject} -> {draft.to}", flush=True)
                continue
            msg = EmailMessage()
            from_header = f"{sender_name} <{cfg.imap.user}>" if sender_name else cfg.imap.user
            msg["From"] = from_header
            msg["To"] = draft.to
            msg["Subject"] = draft.subject
            msg["Date"] = formatdate(time.time(), localtime=True)
            msg.set_content(draft.body, subtype="plain", charset="utf-8")
            typ, _ = client.conn.append(box, "\\Draft",
                                        imaplib.Time2Internaldate(time.time()),
                                        msg.as_bytes())
            if typ == "OK":
                print(f"[pushed] {draft.subject} -> {draft.to}", flush=True)
                pushed += 1
            else:
                print(f"[failed] {draft.subject} -> {draft.to}", flush=True)
    return {"pushed": pushed, "skipped": skipped}


def scan_draft_box(cfg: Config) -> dict:
    """Read-only snapshot of the drafts mailbox as JSON-serialisable data."""
    with ImapClient(cfg.imap) as client:
        box = ImapClient.find_folder(client.conn, "\\Drafts", "草稿")
        client.conn.select(box, readonly=True)
        uids = client.uid_search("ALL")
        out = []
        for uid in uids:
            try:
                msg = client.uid_fetch_headers(uid)
            except ValueError:
                continue
            entry = {
                "subject": client.header(msg, "Subject"),
                "to": client.addresses(msg, "To"),
                "cc": client.addresses(msg, "Cc"),
            }
            if not entry["to"]:
                try:
                    full = client.uid_fetch_full(uid)
                    m = _NAME_RE.search(client.body_text(full))
                    if m:
                        entry["name"] = m.group(1)
                except ValueError:
                    pass
            out.append(entry)
    return {"folder": box, "total": len(out), "drafts": out}


def scan_draft_box_json(cfg: Config) -> str:
    return json.dumps(scan_draft_box(cfg), ensure_ascii=False)
