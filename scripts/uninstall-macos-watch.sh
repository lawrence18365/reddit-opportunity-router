#!/bin/zsh
set -euo pipefail

label="com.lawrence.reddit-opportunity-router"
target="${HOME}/Library/LaunchAgents/${label}.plist"

if [[ -f "${target}" ]]; then
  launchctl bootout "gui/${UID}" "${target}" >/dev/null 2>&1 || true
  mv "${target}" "${HOME}/.Trash/${label}.plist"
  echo "Stopped the watcher and moved its launch file to Trash."
else
  echo "The watcher is not installed."
fi
