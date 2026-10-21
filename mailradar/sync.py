"""Incremental mailbox sync: archive inbound mail and write back status.

For every configured folder the scanner walks new UIDs, matches each message
against the contact book by address, and then:

* inbound (INBOX)  -> archive the message under ``archive/<dir>/<YYYY-MM>/``
  and mark the contact ``replied``;
* outbound (Sent)  -> record send evidence and mark the contact ``sent``
  (no message file is written for the sent folder).

Progress is checkpointed per folder by UID so re-runs are incremental and a
``--full`` rescan stays idempotent (already-archived files are skipped).
"""

from __future__ import annotations

import json
import subprocess
from email.utils import parsedate_to_datetime
from pathlib import Path

from .config import Config
from .contacts import ContactBook, load_contacts, mark_replied, mark_sent
from .imap_client import ImapClient


def _load_state(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _safe_name(text: str, limit: int = 60) -> str:
    import re
    text = re.sub(r"\s+", "_", (text or "").strip())
    text = re.sub(r'[\\/:*?"<>|\r\n]', "_", text)
    return text[:limit] or "untitled"


def _archive_message(client: ImapClient, msg, contact_dir: str, direction: str,
                     out_root: Path, repo_root: Path) -> Path | None:
    """Write one inbound message as .md + .eml. Returns the .md path or None if
    it already exists (idempotent full rescan)."""
    subject = client.header(msg, "Subject")
    date_hdr = msg.get("Date", "")
    try:
        dt = parsedate_to_datetime(date_hdr)
        stamp = dt.strftime("%Y-%m-%d_%H%M%S")
        ym = dt.strftime("%Y-%m")
    except Exception:
        stamp, ym = "unknown", "unknown"
    base = _safe_name(f"{stamp}_{direction}_{subject}")
    target = out_root / contact_dir / ym
    md_path = target / f"{base}.md"
    if md_path.exists():
        return None
    target.mkdir(parents=True, exist_ok=True)

    md = [
        f"# {subject or '(no subject)'}",
        "",
        f"- direction: {'inbound' if direction == 'in' else 'outbound'}",
        f"- from: {client.header(msg, 'From')}",
        f"- to: {client.header(msg, 'To')}",
        f"- cc: {client.header(msg, 'Cc')}",
        f"- date: {date_hdr}",
        f"- message-id: `{(msg.get('Message-ID') or '').strip()}`",
        "",
        "---",
        "",
        client.body_text(msg) or "(empty body)",
        "",
    ]
    md_path.write_text("\n".join(md), encoding="utf-8")
    (target / f"{base}.eml").write_bytes(msg.as_bytes())
    return md_path


def _git_commit(paths: list[Path], repo_root: Path, message: str) -> None:
    rel = [str(p.relative_to(repo_root)) for p in paths]
    subprocess.run(["git", "-C", str(repo_root), "add", "--"] + rel, check=True)
    result = subprocess.run(["git", "-C", str(repo_root), "commit", "-m", message, "--"] + rel)
    if result.returncode != 0:
        print("warning: nothing to commit or git failed; check `git status`", flush=True)


def run_sync(cfg: Config, *, full: bool = False, dry_run: bool = False,
             commit: bool = True) -> dict:
    """Execute one sync pass. Returns a small summary dict."""
    book = load_contacts(cfg)
    out_root = cfg.resolve(cfg.archive.out_dir)
    state_path = cfg.resolve(cfg.archive.state_file)
    state = {} if full else _load_state(state_path)

    sent_hits: dict[str, str] = {}     # email -> earliest MM-DD
    reply_hits: dict[str, str] = {}
    saved = scanned = 0

    with ImapClient(cfg.imap) as client:
        for folder, direction in cfg.imap.folders:
            if not client.select(folder, readonly=True):
                print(f"skip {folder}: cannot open", flush=True)
                continue
            uidvalidity = client.uid_validity()
            fstate = state.get(folder, {})
            if fstate.get("uidvalidity") not in (None, uidvalidity):
                print(f"{folder}: UIDVALIDITY changed, doing a full rescan", flush=True)
                fstate = {}
            known = {int(k) for k in fstate.get("uids", {})}
            max_uid = max(known) if known else 0

            if full or not known:
                uids = client.uid_search("ALL")
            else:
                uids = client.uid_search(f"UID {max_uid + 1}:*")
            uids = [u for u in uids if u not in known]
            print(f"{folder}: {len(uids)} to process", flush=True)

            uids_record = dict(fstate.get("uids", {}))
            for uid in uids:
                try:
                    msg = client.uid_fetch_headers(uid)
                except ValueError:
                    continue
                scanned += 1

                if direction == "in":
                    candidates = client.addresses(msg, "From") + client.addresses(msg, "Reply-To")
                else:
                    candidates = client.addresses(msg, "To") + client.addresses(msg, "Cc")

                # Self-copy in INBOX (sent from another client) counts as send evidence.
                if direction == "in" and set(client.addresses(msg, "From")) & set(cfg.self_addresses):
                    for addr in client.addresses(msg, "To") + client.addresses(msg, "Cc"):
                        if book.lookup(addr):
                            sent_hits[addr] = min(sent_hits.get(addr, "9999"), client.date_mmdd(msg))
                    uids_record.setdefault(str(uid), "")
                    continue

                hit = next((book.lookup(a) for a in candidates if book.lookup(a)), None)
                if hit is None:
                    uids_record.setdefault(str(uid), "")
                    continue
                hit_addr = next(a for a in candidates if book.lookup(a))

                if direction != "in":
                    sent_hits[hit_addr] = min(sent_hits.get(hit_addr, "9999"), client.date_mmdd(msg))
                    print(f"  [sent] {hit.directory} <- {hit_addr}", flush=True)
                else:
                    reply_hits[hit_addr] = min(reply_hits.get(hit_addr, "9999"), client.date_mmdd(msg))
                    if dry_run:
                        print(f"  [dry] {hit.directory} <- {client.header(msg, 'Subject')[:50]}", flush=True)
                    else:
                        try:
                            full_msg = client.uid_fetch_full(uid)
                        except ValueError:
                            print(f"  warning: UID {uid} full fetch failed", flush=True)
                            continue
                        rel = _archive_message(client, full_msg, hit.directory, direction, out_root, cfg.root)
                        if rel is None:
                            uids_record.setdefault(str(uid), "")
                            continue
                        print(f"  archived {direction} {hit.directory}: {rel.name}", flush=True)
                        uids_record[str(uid)] = rel.name
                        saved += 1
                uids_record.setdefault(str(uid), "")
            state[folder] = {"uidvalidity": uidvalidity, "uids": uids_record}
            client.close()

    if not dry_run:
        _save_state(state_path, state)
        if sent_hits:
            changed = mark_sent(book, cfg, sent_hits)
            _maybe_commit(changed, cfg, commit, "sync: mark sent")
        if reply_hits:
            changed = mark_replied(book, cfg, reply_hits)
            _maybe_commit(changed, cfg, commit, "sync: mark replied")

    return {
        "scanned": scanned,
        "archived": saved,
        "sent_hits": len(sent_hits),
        "reply_hits": len(reply_hits),
        "contacts": len(book.by_email),
    }


def _maybe_commit(changed: list[Path], cfg: Config, commit: bool, message: str) -> None:
    if changed:
        print(f"write-back: {len(changed)} table(s): {', '.join(p.name for p in changed)}", flush=True)
        if commit:
            _git_commit(changed, cfg.root, message)
    else:
        print("write-back: no new changes", flush=True)
