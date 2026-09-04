#!/bin/zsh
set -euo pipefail

repo_root="${0:A:h:h}"
notification_env="${SUBSCOPE_NOTIFICATION_ENV:-${XDG_CONFIG_HOME:-${HOME}/.config}/subscope/notifications.env}"

if [[ -f "${notification_env}" ]]; then
  set -a
  source "${notification_env}"
  set +a
fi

exec "${repo_root}/.venv/bin/subscope" portfolio search --days 7
