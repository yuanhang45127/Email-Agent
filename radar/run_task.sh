#!/usr/bin/env bash
# Headless task runner: feed a prompt file to a headless LLM CLI and log it.
#
# The LLM CLI is configurable so you can point this at kimi, claude, or any
# `-p`-style non-interactive runner:
#   export MAILRADAR_LLM_CLI=/root/.kimi-code/bin/kimi
#
# Usage: run_task.sh <prompts/xxx.md> <log-name>
# cron:  7 8 * * * /path/to/radar/run_task.sh scan.md scan.log
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLM_CLI="${MAILRADAR_LLM_CLI:-kimi}"
PROMPTS_DIR="$ROOT/radar/prompts"
LOGS_DIR="$ROOT/radar/logs"

prompt_file="${1:?usage: run_task.sh <prompt.md> <log>}"
log_name="${2:?missing log name}"
mkdir -p "$LOGS_DIR"
log="$LOGS_DIR/$log_name"

cd "$ROOT"
prompt="$(cat "$PROMPTS_DIR/$prompt_file")"
"$LLM_CLI" -p "$prompt" >> "$log" 2>&1
rc=$?
echo "[$(date '+%F %T')] run_task $prompt_file exit=$rc" >> "$log"
exit $rc
