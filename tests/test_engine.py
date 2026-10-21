"""Unit tests for the pure-logic parts of the engine (no IMAP needed).

Run with either:
    python -m unittest discover -s tests
    pytest -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mailradar.config import (
    ArchiveConfig,
    Config,
    ContactsConfig,
    ImapConfig,
    NtfyConfig,
    NotifyConfig,
    WeixinConfig,
    extract_emails,
)
from mailradar.contacts import load_contacts, mark_replied, mark_sent
from mailradar.drafts import parse_draft


def make_config(root: Path) -> Config:
    return Config(
        root=root,
        imap=ImapConfig(host="h", user="u", password="p"),
        contacts=ContactsConfig(sources=["contacts/*.csv"]),
        archive=ArchiveConfig(),
        notify=NotifyConfig(ntfy=NtfyConfig(), weixin=WeixinConfig()),
        self_addresses=[],
    )


class ExtractEmails(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(extract_emails("a@b.com, c@d.org"), ["a@b.com", "c@d.org"])

    def test_with_notes(self):
        self.assertEqual(extract_emails("x@y.com（退信）"), ["x@y.com"])

    def test_empty(self):
        self.assertEqual(extract_emails(None), [])
        self.assertEqual(extract_emails("no email here"), [])


class Contacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "contacts").mkdir()
        (self.root / "contacts" / "demo.csv").write_text(
            "email,name,org,dir,sent,replied,status\n"
            "alice@x.com,Alice,OrgA,alice,,,未接触\n"
            "bob@y.com,Bob,OrgB,bob,是,,已发送待回复（10-15首封）\n",
            encoding="utf-8",
        )
        self.cfg = make_config(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lookup(self):
        book = load_contacts(self.cfg)
        self.assertEqual(book.lookup("alice@x.com").directory, "alice")
        self.assertIsNone(book.lookup("nobody@z.com"))

    def test_mark_sent_and_replied(self):
        book = load_contacts(self.cfg)
        changed = mark_sent(book, self.cfg, {"alice@x.com": "10-21"})
        self.assertEqual(len(changed), 1)
        text = (self.root / "contacts" / "demo.csv").read_text(encoding="utf-8")
        self.assertIn("alice,是,", text)  # sent flipped to truthy, ✉ prefix on dir

        book = load_contacts(self.cfg)
        changed = mark_replied(book, self.cfg, {"bob@y.com": "10-20"})
        text = (self.root / "contacts" / "demo.csv").read_text(encoding="utf-8")
        self.assertIn("已回复（10-20）", text)


class DraftParsing(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "d.md"
            p.write_text("**主题**：Re: hi\n**收件人**：a@b.com\n```\nbody\n```\n", encoding="utf-8")
            draft = parse_draft(p)
            self.assertEqual(draft.subject, "Re: hi")
            self.assertEqual(draft.to, "a@b.com")
            self.assertEqual(draft.body, "body")

    def test_missing_field_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "d.md"
            p.write_text("**主题**：Re: hi\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_draft(p)


if __name__ == "__main__":
    unittest.main()
