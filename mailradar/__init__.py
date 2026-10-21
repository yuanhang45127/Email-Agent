"""MailRadar — self-hosted mail automation & information-watch agent.

A small, configuration-driven toolkit that links a mailbox to a structured
contact table: it archives inbound mail, writes back send/reply status, manages
an IMAP drafts queue, fans notifications out to ntfy and WeChat, and drives
headless-LLM "radar" tasks on a schedule.
"""

from .config import Config, load_config  # noqa: F401

__version__ = "0.1.0"
