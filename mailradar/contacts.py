"""Contact table loading and status write-back.

A *contact table* is any CSV with (at least) an email column. MailRadar maps
each email address to its row so that incoming/outgoing mail can be linked
back to a contact and its status columns updated in place.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, extract_emails


@dataclass
class Contact:
    row: dict[str, str]
    source: Path          # CSV file the row lives in
    row_index: int        # 0-based data row index within that file
    directory: str = ""   # archive sub-directory name for this contact

    @property
    def emails(self) -> set[str]:
        return set(extract_emails(self.row.get("__email__", "")))


@dataclass
class ContactBook:
    """All contacts across every configured source table."""

    by_email: dict[str, Contact] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)

    def lookup(self, addr: str) -> Contact | None:
        return self.by_email.get(addr.lower())


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, reader.fieldnames or []


def load_contacts(cfg: Config) -> ContactBook:
    """Build the email -> Contact index from every configured source table."""
    book = ContactBook()
    email_col = cfg.contacts.columns.get("email", "email")
    dir_col = cfg.contacts.columns.get("dir", "dir")

    for pattern in cfg.contacts.sources:
        for path in sorted(cfg.root.glob(pattern)):
            rows, fieldnames = _read_rows(path)
            if email_col not in fieldnames:
                continue
            book.sources.append(path)
            for i, row in enumerate(rows):
                directory = (row.get(dir_col) or "").strip()
                if directory.endswith(".md"):
                    directory = directory[:-3]
                contact = Contact(
                    row=row,
                    source=path,
                    row_index=i,
                    directory=directory or _fallback_dir(row, cfg),
                )
                # Stash the raw email cell for later matching.
                contact.row["__email__"] = row.get(email_col, "")
                for addr in extract_emails(row.get(email_col, "")):
                    if addr in book.by_email and book.by_email[addr].directory != directory:
                        # First table wins on conflict; keep behaviour predictable.
                        continue
                    book.by_email[addr] = contact
    return book


def _fallback_dir(row: dict[str, str], cfg: Config) -> str:
    name = (row.get(cfg.contacts.columns.get("name", "name")) or "unknown").strip()
    return name or "unknown"


def _rewrite(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    clean = [{k: v for k, v in r.items() if not k.startswith("__")} for r in rows]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(clean)


def mark_sent(book: ContactBook, cfg: Config, hits: dict[str, str]) -> list[Path]:
    """Set the ``sent`` column to the truthy value for contacts in ``hits``.

    ``hits`` maps email -> earliest MM-DD. Also refreshes a stale status cell
    and prefixes the directory with a sent marker. Returns changed CSV paths.
    """
    return _mark(book, cfg, hits, column="sent", status_template="已发送待回复（{date}首封）")


def mark_replied(book: ContactBook, cfg: Config, hits: dict[str, str]) -> list[Path]:
    """Set the ``replied`` column for contacts that wrote back. Returns changed paths."""
    return _mark(book, cfg, hits, column="replied", status_template="已回复（{date}）")


def _mark(book: ContactBook, cfg: Config, hits: dict[str, str], *, column: str,
          status_template: str) -> list[Path]:
    col_name = cfg.contacts.columns.get(column, column)
    status_col = cfg.contacts.columns.get("status", "status")
    dir_col = cfg.contacts.columns.get("dir", "dir")
    truthy = cfg.contacts.truthy

    # Group touched contacts by source file so each file is rewritten once.
    by_source: dict[Path, list[Contact]] = {}
    for addr in hits:
        contact = book.lookup(addr)
        if contact is not None:
            by_source.setdefault(contact.source, []).append(contact)

    changed: list[Path] = []
    for path, contacts in by_source.items():
        rows, fieldnames = _read_rows(path)
        if col_name not in fieldnames:
            continue
        dirty = False
        for contact in contacts:
            row = rows[contact.row_index]
            hit_addrs = contact.emails & hits.keys()
            if not hit_addrs:
                continue
            if (row.get(col_name) or "").strip() != truthy:
                row[col_name] = truthy
                dirty = True
            date = min(hits[a] for a in hit_addrs)
            status = row.get(status_col) or ""
            if column == "sent":
                if "已发送待回复" not in status and "已回复" not in status:
                    row[status_col] = status_template.format(date=date)
                    dirty = True
                directory = row.get(dir_col) or ""
                if directory and not directory.endswith(".md") and "✉" not in directory:
                    row[dir_col] = "✉" + directory
                    dirty = True
            else:  # replied
                if "已发送待回复" in status and "已回复" not in status:
                    row[status_col] = status_template.format(date=date)
                    dirty = True
        if dirty:
            _rewrite(path, rows, fieldnames)
            changed.append(path)
    return changed
