#!/bin/bash
# Auto-update performance_log.json — runs via cron every 15 minutes (offset by 1 min).
# Runs the full pipeline demo first, then the performance ledger.
# Commits and pushes performance_log.json to GitHub.
#
# Cron entry:
#   1,16,31,46 * * * * /home/postfiat/pf-regime-sdk/update_ledger.sh >> /home/postfiat/ledger_cron.log 2>&1

set -uo pipefail

REPO_DIR="/home/postfiat/pf-regime-sdk"
LEDGER_FILE="$REPO_DIR/performance_log.json"
LOG_PREFIX="[$(date -u +%Y-%m-%dT%H:%M:%SZ)]"

cd "$REPO_DIR"
source "$REPO_DIR/git_sync.sh"
pf_lock

export PF_API_URL="http://localhost:8080"

# Stage 1: Run pipeline (exit codes 1/2 are valid — dont fail on them)
echo "$LOG_PREFIX Running pipeline..."
python3 examples/full_pipeline_demo.py --url="$PF_API_URL" > /dev/null 2>&1 || true

# Stage 2: Run ledger
echo "$LOG_PREFIX Running ledger..."
python3 performance_ledger.py 2>&1 | while read line; do echo "$LOG_PREFIX $line"; done

# Stage 3: Commit and push if changed
if git diff --quiet "$LEDGER_FILE" 2>/dev/null && ! git ls-files --others --exclude-standard | grep -q "performance_log.json"; then
    echo "$LOG_PREFIX No changes to performance_log.json — skipping commit"
    exit 0
fi

pf_commit_push "$LEDGER_FILE" "Auto-update performance_log.json $(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 1
