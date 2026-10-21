# Architecture

MailRadar is a small engine plus a few thin adapters. This document describes
how the pieces fit and the invariants they maintain.

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│  adapters                                                    │
│  cli.py · radar/run_task.sh · radar/notify.sh · deploy/      │
├─────────────────────────────────────────────────────────────┤
│  engine (mailradar/)                                         │
│  config · imap_client · contacts · sync · drafts · notify    │
├─────────────────────────────────────────────────────────────┤
│  external systems                                            │
│  IMAP server · CSV contact tables · ntfy · OpenClaw gateway  │
└─────────────────────────────────────────────────────────────┘
```

The engine has no knowledge of *what* your contacts are — collaborators,
vendors, classmates, anything. It only knows a table with an email column and a
handful of status columns. All domain meaning lives in configuration and in the
optional skill contracts under `skills/`.

## Data flow: sync

`sync` is the heart of the system. For each configured folder it walks new UIDs
and matches every message against the contact book by address:

```
INBOX  ──match by From/Reply-To──►  archive .md + .eml  ──►  mark replied
Sent   ──match by To/Cc──────────►  (no file)            ──►  mark sent
INBOX self-copy (From == you) ─────►  (no file)          ──►  mark sent
```

Invariants:

- **Incremental.** A per-folder UID checkpoint (`.mailradar_state.json`) means
  each run only fetches mail newer than the last seen UID.
- **Idempotent.** A `--full` rescan re-derives everything but skips files that
  already exist (same subject + timestamp), so it never double-archives.
- **Header-first.** Only headers are fetched to decide a match; the full body is
  downloaded only when a message is actually going to be archived. This keeps
  large-attachment mail from being pulled unnecessarily.
- **Write-back is per-file.** Each touched CSV is rewritten once, then committed
  on its own so history stays reviewable.

## Data flow: drafts

Drafts are local markdown files with a tiny convention (`**主题**` / `**收件人**`
/ a fenced body). `push` parses them, de-duplicates against the drafts mailbox
by (subject, recipient), and APPENDs — never deletes. `scan-box` is a read-only
JSON snapshot used by UIs to show "in the box, not yet sent".

## Data flow: radar

The radar is a *prompt-driven* agent, not code. `run_task.sh` feeds a prompt to
a headless LLM CLI; the prompt (`radar/prompts/scan.md`) enforces the safety
model in natural language:

- read-only sub-agents that return structured results;
- content-hash de-duplication against `radar/state.json`;
- "never invent an item a sub-agent didn't return";
- notify only for genuinely new entries.

Keeping the watch logic in a prompt (rather than bespoke scrapers) trades some
determinism for a lot of flexibility — you can retarget it at new sources by
editing `radar/sources.md`.

## The human-in-the-loop contract

This is the design decision the whole project orbits. Three rules, enforced at
different layers:

1. **No auto-send.** Nothing in the engine calls SMTP/Send. The drafts box is
   the terminal state; a human presses send in their mail client.
2. **Evaluate before act.** The `skills/outreach-eval` contract splits any
   outbound action into a read-only evaluation phase and a post-confirmation
   draft phase, with hard gates (already-sent, bounced, unverified address,
   business rules) that block before any work happens.
3. **Dry-run by default.** Every destructive or outward path (`push`, status
   write-back commits) previews first; `--apply` is explicit.

## Notify

`notify` fans a (title, body) out to configured channels and is strictly
best-effort — a failing channel is captured in the result, never raised, so a
notification problem cannot break the sync or radar task that triggered it.

- **ntfy** — a plain HTTP POST to a self-hosted topic.
- **weixin** — shells out to `openclaw agent … --deliver` to drop the message
  into a WeChat chat via an OpenClaw gateway.

## Failure model

- IMAP connect/auth failure → the sync command exits non-zero; systemd marks the
  unit failed. No partial state is written (state is saved only after a full
  pass).
- State file corruption → treated as "no checkpoint", i.e. a full rescan, which
  is safe because archiving is idempotent.
- Notify failure → logged in the per-channel result, pipeline continues.
- Radar LLM failure → the run_task wrapper records a non-zero exit in the log;
  scheduling/watchdog is left to systemd.
