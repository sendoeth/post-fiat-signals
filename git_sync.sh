#!/bin/bash
# Shared commit+push for update_ledger.sh and update_status.sh.
# Both jobs commit to the same public repo, so they hold one lock for the whole
# generate -> commit -> rebase -> push sequence, and every step's failure is reported.
# The token is read by an askpass helper inside .git/, never placed in a URL.

PF_REPO_DIR="/home/postfiat/pf-regime-sdk"
PF_LOCK="$PF_REPO_DIR/.git/pf-sync.lock"
PF_ASKPASS="$PF_REPO_DIR/.git/pf-askpass.sh"

pf_log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# Call first in each job: serializes both cron jobs (waits up to 10 min).
pf_lock() {
    exec 9>"$PF_LOCK"
    if ! flock -w 600 9; then
        pf_log "ERROR: could not acquire $PF_LOCK within 600s - skipping run"
        exit 1
    fi
}

pf_askpass() {
    if [ ! -x "$PF_ASKPASS" ]; then
        printf '#!/bin/sh\ncase "$1" in Username*) echo sendoeth;; *) cat /home/postfiat/.github_token;; esac\n' > "$PF_ASKPASS"
        chmod 700 "$PF_ASKPASS"
    fi
}

# pf_commit_push <file> <message>: returns non-zero (and says why) on any failure.
pf_commit_push() {
    local file="$1" msg="$2"
    # Server addresses must never reach the public repo.
    if grep -qE '84\.32\.[0-9]{1,3}\.[0-9]{1,3}' "$file"; then
        pf_log "ERROR: $file contains a server address - refusing to commit (fix the generator)"
        return 1
    fi
    if ! git add "$file" || ! git -c commit.gpgsign=false commit -q -m "$msg"; then
        pf_log "ERROR: commit of $file failed"
        return 1
    fi
    pf_askpass
    if ! GIT_ASKPASS="$PF_ASKPASS" GIT_TERMINAL_PROMPT=0 git pull -q --rebase --autostash origin main; then
        git rebase --abort 2>/dev/null
        pf_log "ERROR: rebase onto origin/main failed (conflict) - local commit kept, manual fix needed"
        return 1
    fi
    if ! GIT_ASKPASS="$PF_ASKPASS" GIT_TERMINAL_PROMPT=0 git push -q origin main; then
        pf_log "ERROR: push of $file failed - local commit kept, will retry next run"
        return 1
    fi
    pf_log "Pushed $file ($(git rev-parse --short HEAD))"
}
