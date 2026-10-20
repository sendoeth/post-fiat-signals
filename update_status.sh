#!/bin/bash
# Auto-update status.json — runs via cron every 15 minutes.
# Regenerates health status from the live API, and if the overall_health
# state or any component state changed, commits and pushes to GitHub.
#
# Cron entry (added by setup):
#   */15 * * * * /home/postfiat/pf-regime-sdk/update_status.sh >> /home/postfiat/status_cron.log 2>&1

set -euo pipefail

REPO_DIR="/home/postfiat/pf-regime-sdk"
STATUS_FILE="$REPO_DIR/status.json"
LOG_PREFIX="[$(date -u +%Y-%m-%dT%H:%M:%SZ)]"

cd "$REPO_DIR"
source "$REPO_DIR/git_sync.sh"
pf_lock

# Capture previous state fingerprint (overall + component states)
OLD_FINGERPRINT=""
if [ -f "$STATUS_FILE" ]; then
    OLD_FINGERPRINT=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATUS_FILE'))
    parts = [d.get('overall_health', '')]
    for name in sorted(d.get('components', {}).keys()):
        parts.append(d['components'][name].get('state', ''))
    print('|'.join(parts))
except: print('')
" 2>/dev/null || echo "")
fi

# Regenerate status.json from live API
export PF_API_URL="http://localhost:8080"
export PF_PUBLIC_URL="http://<signal-api-host>:8080"
python3 generate_status.py --out "$STATUS_FILE" 2>&1 | while read line; do echo "$LOG_PREFIX $line"; done

# Check new fingerprint
NEW_FINGERPRINT=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATUS_FILE'))
    parts = [d.get('overall_health', '')]
    for name in sorted(d.get('components', {}).keys()):
        parts.append(d['components'][name].get('state', ''))
    print('|'.join(parts))
except: print('')
" 2>/dev/null || echo "")

# Always update the timestamp even if states didnt change
# (builders want to see recent generated_at to know the system is alive)
if git diff --quiet "$STATUS_FILE" 2>/dev/null; then
    echo "$LOG_PREFIX No changes to status.json — skipping commit"
    exit 0
fi

# Commit and push (shared lock, rebase, real failure reporting)
OVERALL=$(python3 -c "import json; print(json.load(open('$STATUS_FILE')).get('overall_health', 'UNKNOWN'))")
pf_commit_push "$STATUS_FILE" "Auto-update status.json — $OVERALL $(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 1

if [ "$OLD_FINGERPRINT" != "$NEW_FINGERPRINT" ]; then
    echo "$LOG_PREFIX STATE CHANGE: $OLD_FINGERPRINT -> $NEW_FINGERPRINT"
fi
