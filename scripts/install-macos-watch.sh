#!/bin/zsh
set -euo pipefail

repo_root="${0:A:h:h}"
label="com.lawrence.reddit-opportunity-router"
template="${repo_root}/deploy/${label}.plist.template"
launch_agents="${HOME}/Library/LaunchAgents"
log_dir="${HOME}/Library/Logs"
target="${launch_agents}/${label}.plist"
runner="${repo_root}/scripts/portfolio-scan.sh"

mkdir -p "${launch_agents}" "${log_dir}"
sed \
  -e "s|__REPO_ROOT__|${repo_root}|g" \
  -e "s|__RUNNER__|${runner}|g" \
  -e "s|__LOG_DIR__|${log_dir}|g" \
  "${template}" > "${target}"

launchctl bootout "gui/${UID}" "${target}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID}" "${target}"
launchctl enable "gui/${UID}/${label}"

echo "Installed ${label}. It scans every 15 minutes and alerts on new matches."
echo "Logs: ${log_dir}/reddit-opportunity-router.log"
