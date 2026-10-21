#!/usr/bin/env bash
# Notification wrapper for radar tasks. Fans out to every channel configured
# in config.yaml via the mailradar CLI, so ntfy and WeChat stay in one place.
#
# Usage: notify.sh "标题" "正文" [--channels ntfy,weixin]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec python3 -m mailradar notify "$@"
