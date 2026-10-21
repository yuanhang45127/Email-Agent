"""Thin IMAP helper: connect, decode headers, list and fetch messages.

Everything here is read-only unless a method explicitly says otherwise.
"""

from __future__ import annotations

import imaplib
import re
from email import policy
from email.parser import BytesParser
from email.header import decode_header
from email.utils import getaddresses, parsedate_to_datetime
from typing import Iterable

from .config import ImapConfig


def decode_header_value(value: str | None) -> str:
    """Decode an RFC 2047 encoded header into a plain string."""
    if not value:
        return ""
    out: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


class ImapClient:
    """Context manager wrapping an IMAP4_SSL connection."""

    def __init__(self, cfg: ImapConfig):
        self.cfg = cfg
        self.conn = imaplib.IMAP4_SSL(cfg.host, cfg.port)

    def __enter__(self) -> "ImapClient":
        self.conn.login(self.cfg.user, self.cfg.password)
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self.conn.logout()
        except Exception:
            pass

    # -- low level -------------------------------------------------------
    def select(self, folder: str, readonly: bool = True) -> bool:
        typ, _ = self.conn.select(folder, readonly=readonly)
        return typ == "OK"

    def uid_validity(self) -> int:
        typ, data = self.conn.response("UIDVALIDITY")
        if typ == "OK" and data and data[0]:
            return int(data[0].decode().split()[-1].strip(")"))
        return 0

    def uid_search(self, criterion: str) -> list[int]:
        typ, data = self.conn.uid("search", None, criterion)
        if typ != "OK" or not data or not data[0]:
            return []
        return [int(u) for u in data[0].split()]

    def uid_fetch_headers(self, uid: int) -> BytesParser:
        typ, data = self.conn.uid(
            "fetch", str(uid),
            "(BODY.PEEK[HEADER.FIELDS (FROM TO CC REPLY-TO SUBJECT DATE MESSAGE-ID)])",
        )
        if typ != "OK" or not data or not data[0]:
            raise ValueError(f"header fetch failed for UID {uid}")
        return BytesParser(policy=policy.default).parsebytes(data[0][1])

    def uid_fetch_full(self, uid: int) -> BytesParser:
        typ, data = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not data or not data[0]:
            raise ValueError(f"full fetch failed for UID {uid}")
        return BytesParser(policy=policy.default).parsebytes(data[0][1])

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def addresses(msg, header: str) -> list[str]:
        """Lower-cased address list from a header (From/To/Cc/Reply-To)."""
        return [a.lower() for _, a in getaddresses(msg.get_all(header, [])) if a]

    @staticmethod
    def header(msg, name: str) -> str:
        val = msg.get(name, "")
        return decode_header_value(val) if val else ""

    @staticmethod
    def date_mmdd(msg) -> str:
        try:
            return parsedate_to_datetime(msg.get("Date", "")).strftime("%m-%d")
        except Exception:
            return "日期未知"

    @staticmethod
    def body_text(msg) -> str:
        """Best-effort plain-text body (strips HTML tags when needed)."""
        if msg.is_multipart():
            for part in msg.walk():
                disp = str(part.get("Content-Disposition") or "")
                if part.get_content_type() == "text/plain" and "attachment" not in disp.lower():
                    try:
                        return part.get_content()
                    except Exception:
                        pass
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        return re.sub(r"<[^>]+>", "", part.get_content())
                    except Exception:
                        pass
        else:
            try:
                return msg.get_content()
            except Exception:
                pass
        return ""

    @staticmethod
    def find_folder(conn: imaplib.IMAP4_SSL, *keywords: str) -> str:
        """Locate a mailbox by attribute/keyword (e.g. ``\\Drafts`` or 草稿)."""
        typ, boxes = conn.list()
        if typ != "OK":
            raise RuntimeError("IMAP LIST failed")
        for b in boxes:
            text = b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
            if any(k in text for k in keywords):
                m = re.search(r'"([^"]+)"\s*$', text)
                return m.group(1) if m else text.split()[-1].strip('"')
        raise RuntimeError(f"mailbox not found for keywords: {keywords}")


def iter_addresses(msg, *headers: str) -> Iterable[str]:
    for h in headers:
        yield from ImapClient.addresses(msg, h)
