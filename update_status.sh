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

# Commit and push
git add "$STATUS_FILE"
git commit -m "Auto-update status.json — $(python3 -c "
import json
d = json.load(open('$STATUS_FILE'))
print(d.get('overall_health', 'UNKNOWN'))
") $(date -u +%Y-%m-%dT%H:%M:%SZ)" --no-gpg-sign 2>&1 | while read line; do echo "$LOG_PREFIX $line"; done

# Token read from file outside repo — never committed
GITHUB_TOKEN=$(cat /home/postfiat/.github_token 2>/dev/null || echo "")
if [ -z "$GITHUB_TOKEN" ]; then
    echo "$LOG_PREFIX ERROR: No token found at /home/postfiat/.github_token — cannot push"
    exit 1
fi
git push "https://${GITHUB_TOKEN}@github.com/sendoeth/post-fiat-signals.git" main 2>&1 | while read line; do echo "$LOG_PREFIX $line"; done

echo "$LOG_PREFIX Pushed updated status.json (overall: $NEW_FINGERPRINT)"

if [ "$OLD_FINGERPRINT" != "$NEW_FINGERPRINT" ]; then
    echo "$LOG_PREFIX STATE CHANGE: $OLD_FINGERPRINT -> $NEW_FINGERPRINT"
fi
