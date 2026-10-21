# MailRadar

**A self-hosted mail-automation and information-watch agent.** It links your
mailbox to a structured contact table, archives inbound mail, writes back
send/reply status, manages an IMAP drafts queue, fans notifications out to
**ntfy** and **WeChat**, and drives headless-LLM "radar" tasks on a schedule.

MailRadar is built around one principle: **the agent prepares, the human
decides.** It never sends mail or messages on your behalf — its outputs stop at
a drafts box or a notification, and every irreversible step waits for a human.

```
mailbox (IMAP)  ──►  contact table (CSV)  ──►  status write-back
      │                    │
      │                    └──►  drafts queue (append-only, de-duplicated)
      │
      └──►  radar (headless LLM)  ──►  dedup  ──►  ntfy / WeChat notify
```

## Why

Most "email assistant" tools either fully automate (and eventually send
something you didn't mean to) or stay manual. MailRadar takes the middle path:
it does the tedious, reversible work — archiving, linking, tracking, watching —
and hands you a clean drafts box and a digest. You keep the send button.

## Features

- **Mailbox ↔ contact linking.** Any CSV with an email column becomes a live
  contact book. Inbound mail is matched by address and archived under
  `archive/<contact>/<YYYY-MM>/`; the `sent` / `replied` / `status` columns are
  written back in place.
- **Incremental, idempotent sync.** UID checkpointing per folder means re-runs
  only touch new mail, and a `--full` rescan never double-archives.
- **Drafts queue.** Parse local markdown drafts and append them to the IMAP
  drafts box, de-duplicated by (subject, recipient) so it's safe to re-run.
- **Information radar.** A headless-LLM task scans a watch list on a schedule,
  de-duplicates by content hash, and pushes only genuinely new items.
- **Multi-channel notify.** ntfy for reliable phone push; WeChat delivery
  through an [OpenClaw](https://docs.openclaw.ai) gateway.
- **Human-in-the-loop by construction.** No auto-send, no auto-delete of your
  data; destructive actions are dry-run by default.

## Quick start

```bash
git clone <your-repo> && cd MailRadar
pip install -e .

cp config.example.yaml config.yaml   # edit imap + contacts
cp .env.example .env                 # set MAILRADAR_IMAP_PASSWORD

# copy the example contact table into place
mkdir -p contacts && cp examples/contacts.csv contacts/

python -m mailradar sync --dry-run   # preview
python -m mailradar sync             # archive + write back
```

## Configuration

Everything lives in `config.yaml` (see [`config.example.yaml`](config.example.yaml)).
The IMAP password is read from `MAILRADAR_IMAP_PASSWORD` (env or `.env`), never
from the committed file. Column names are fully remappable, so the same engine
works whether your table calls the flag `sent`, `已发送`, or `contacted`.

## WeChat delivery (OpenClaw)

If you run an OpenClaw gateway with the WeChat channel, MailRadar can deliver
notifications straight into your WeChat chat:

```yaml
notify:
  channels: ["ntfy", "weixin"]
  weixin:
    enabled: true
    channel: "openclaw-weixin"
    to: "your-user@im.wechat"
```

```bash
python -m mailradar notify "每日摘要" "3 条待跟进" --channels weixin
```

Under the hood this shells out to
`openclaw agent -m <msg> --channel openclaw-weixin --to <user> --deliver`.
The reverse direction — issuing commands to MailRadar from WeChat — is a matter
of pointing an OpenClaw agent at the `mailradar` CLI as a tool.

## Radar (scheduled watch)

```bash
# edit radar/sources.md with your watch list, then:
radar/run_task.sh scan.md scan.log        # one-shot
# or schedule it — see deploy/mailradar-radar.{service,timer}
```

The prompt (`radar/prompts/scan.md`) is deliberately conservative: read-only
sub-agents, content-hash de-duplication, "never invent an item a sub-agent
didn't return", and notifications only for genuinely new entries.

## CLI

```
python -m mailradar sync [--full] [--dry-run] [--no-commit]
python -m mailradar push <draft.md>... [--apply] [--sender-name NAME]
python -m mailradar scan-box                 # read-only JSON of the drafts box
python -m mailradar notify "标题" "正文" [--channels ntfy,weixin]
```

## Project layout

```
mailradar/          # engine (config, imap, contacts, sync, drafts, notify, cli)
radar/              # scheduled headless-LLM watch (run_task.sh, prompts, sources.md)
skills/             # human-in-the-loop skill contracts (evaluate-before-act)
deploy/             # systemd units for sync + radar
examples/           # sample contact table and draft
tests/              # unit tests for the pure-logic core
```

## Design notes

- **Append-only drafts, dry-run by default.** `push` and every destructive
  path default to a preview; `--apply` is explicit.
- **Atomic writes.** State files and JSON are written via tmp+rename so a crash
  never leaves a half-written file to be read as empty.
- **Best-effort notify.** A failing notification channel is reported, never
  raised — a notify problem can't break the sync that triggered it.
- **Config over convention.** Paths, columns, folders, and channels are all
  data, not hard-coded, so the engine stays generic.

## Testing

```bash
python -m unittest discover -s tests   # or: pytest -q
```

## License

[MIT](LICENSE)
