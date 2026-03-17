#!/usr/bin/env python3
"""Signal Performance Ledger — forward-testing proof layer.

Logs every pipeline decision in real time:
  - NO_TRADE entries during STOP (proving capital preservation discipline)
  - EXECUTE entries with live prices and 14-day outcome evaluation

Reads pipeline_output.json (written by full_pipeline_demo.py) and appends
structured entries to performance_log.json. Deduplicates via 15-min cycle
keys. Evaluates PENDING entries past their 14-day horizon by re-fetching
prices from CoinGecko/Yahoo.

Schema: pf-performance-log/v1
SPI-compatible fields (ticker, action, confidence, horizon_hours) included
for direct b1e55ed adapter consumption.

Usage:
    # Run pipeline first, then ledger
    python3 examples/full_pipeline_demo.py --url http://localhost:8080
    python3 performance_ledger.py

    # Or specify paths
    python3 performance_ledger.py --pipeline-output pipeline_output.json \\
                                  --ledger performance_log.json \\
                                  --url http://localhost:8080
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone, timedelta


# ── Config ─────────────────────────────────────────────────────────────────

SCHEMA = "pf-performance-log/v1"
HORIZON_HOURS = 336  # 14 days
CYCLE_INTERVAL_MIN = 15
STALE_THRESHOLD_SEC = 300  # 5 min — re-run pipeline if output older than this

# CoinGecko coin_id -> symbol mapping
CRYPTO_MAP = {
    "render-token": "RNDR",
    "bittensor": "TAO",
    "akash-network": "AKT",
    "fetch-ai": "FET",
}
CRYPTO_REVERSE = {v: k for k, v in CRYPTO_MAP.items()}

# Semi tickers we track
SEMI_TICKERS = {"NVDA", "AMD", "AVGO", "MRVL", "ASML", "TSM"}


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    """Timestamped stderr log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", file=sys.stderr)


def fetch_crypto_price(symbol):
    """Fetch current price from CoinGecko. Returns float or None."""
    coin_id = CRYPTO_REVERSE.get(symbol)
    if not coin_id:
        log(f"Unknown crypto symbol: {symbol}")
        return None
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currency=usd"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return float(data[coin_id]["usd"])
    except Exception as e:
        log(f"CoinGecko fetch failed for {symbol} ({coin_id}): {e}")
        return None


def fetch_semi_price(ticker):
    """Fetch current price from Yahoo Finance. Returns float or None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        meta = data["chart"]["result"][0]["meta"]
        return float(meta["regularMarketPrice"])
    except Exception as e:
        log(f"Yahoo fetch failed for {ticker}: {e}")
        return None


def get_cycle_key(dt):
    """Round datetime to nearest 15-min boundary, return ISO string."""
    minute = dt.minute
    rounded = (minute // CYCLE_INTERVAL_MIN) * CYCLE_INTERVAL_MIN
    aligned = dt.replace(minute=rounded, second=0, microsecond=0)
    return aligned.strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_exists(entries, cycle_key):
    """Check if an entry with this cycle_key already exists."""
    return any(e.get("cycle_key") == cycle_key for e in entries)


def read_pipeline_output(path):
    """Load and validate pipeline_output.json. Returns dict or None."""
    if not os.path.exists(path):
        log(f"Pipeline output not found: {path}")
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        # Validate minimum fields
        if "watchdog" not in data or "overall" not in data:
            log(f"Pipeline output missing required fields")
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        log(f"Failed to read pipeline output: {e}")
        return None


def run_pipeline(repo_dir, api_url):
    """Run full_pipeline_demo.py as subprocess fallback. Returns True if ran."""
    script = os.path.join(repo_dir, "examples", "full_pipeline_demo.py")
    if not os.path.exists(script):
        log(f"Pipeline script not found: {script}")
        return False
    try:
        log("Running pipeline subprocess...")
        result = subprocess.run(
            [sys.executable, script, f"--url={api_url}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        log(f"Pipeline exited with code {result.returncode}")
        return True
    except Exception as e:
        log(f"Pipeline subprocess failed: {e}")
        return False


def fetch_regime_fallback(api_url):
    """GET /regime/current when scanner is None (STOP path). Returns regime dict or None."""
    url = f"{api_url.rstrip('/')}/regime/current"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return {
            "regime": data.get("id", "UNKNOWN"),
            "confidence": data.get("confidence", 0),
        }
    except Exception as e:
        log(f"Regime fallback fetch failed: {e}")
        return None


def generate_signal_id(pair, cycle_key):
    """Deterministic signal_id from pair + cycle_key."""
    raw = f"{pair}:{cycle_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def extract_crypto_symbol(pair):
    """Extract crypto symbol from pair like 'NVDA/RNDR' -> 'RNDR'."""
    parts = pair.split("/")
    if len(parts) == 2:
        return parts[1]
    return None


def extract_semi_ticker(pair):
    """Extract semi ticker from pair like 'NVDA/RNDR' -> 'NVDA'."""
    parts = pair.split("/")
    if len(parts) == 2:
        return parts[0]
    return None


def build_no_trade_entry(pipeline, cycle_key, ts, regime_info=None):
    """Construct NO_TRADE entry from STOP pipeline output."""
    watchdog = pipeline.get("watchdog", {})
    overall = pipeline.get("overall", {})

    # Get regime from scanner if available, else from fallback
    regime = "UNKNOWN"
    confidence = 0
    scanner = pipeline.get("scanner")
    if scanner and isinstance(scanner, dict):
        regime = scanner.get("regime", "UNKNOWN")
        confidence = scanner.get("confidence", 0)
    elif regime_info:
        regime = regime_info.get("regime", "UNKNOWN")
        confidence = regime_info.get("confidence", 0)

    return {
        "schema": SCHEMA,
        "cycle_key": cycle_key,
        "timestamp": ts,
        "decision": "NO_TRADE",
        "regime": regime,
        "regime_confidence": confidence,
        "watchdog_verdict": watchdog.get("verdict", "UNKNOWN"),
        "signal_fidelity": watchdog.get("signal_fidelity", "UNKNOWN"),
        "regime_confidence_verdict": watchdog.get("regime_confidence", "UNKNOWN"),
        "note": overall.get("position_note", ""),
        "action": "NO_TRADE",
        "horizon_hours": HORIZON_HOURS,
    }


def build_execute_entry(pipeline, signal_decision, cycle_key, ts):
    """Construct EXECUTE entry with live prices for a single signal."""
    watchdog = pipeline.get("watchdog", {})
    scanner = pipeline.get("scanner", {})

    pair = signal_decision.get("pair", "")
    crypto_symbol = extract_crypto_symbol(pair)
    semi_ticker = extract_semi_ticker(pair)

    # Fetch live prices
    crypto_price = fetch_crypto_price(crypto_symbol) if crypto_symbol else None
    semi_price = fetch_semi_price(semi_ticker) if semi_ticker else None

    # Compute eval_due (14 days from now)
    entry_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    eval_due = (entry_dt + timedelta(hours=HORIZON_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    signal_id = generate_signal_id(pair, cycle_key)

    hit_rate = signal_decision.get("hit_rate", 0)
    avg_return = signal_decision.get("avg_return", 0)

    return {
        "schema": SCHEMA,
        "cycle_key": cycle_key,
        "timestamp": ts,
        "decision": "EXECUTE",
        "regime": scanner.get("regime", "UNKNOWN"),
        "regime_confidence": scanner.get("confidence", 0),
        "watchdog_verdict": watchdog.get("verdict", "UNKNOWN"),
        "signal_fidelity": watchdog.get("signal_fidelity", "UNKNOWN"),
        "regime_confidence_verdict": watchdog.get("regime_confidence", "UNKNOWN"),
        "note": signal_decision.get("reason", ""),
        "signal_id": signal_id,
        "pair": pair,
        "ticker": crypto_symbol or "",
        "semi_ticker": semi_ticker or "",
        "action": "BUY",
        "signal_type": signal_decision.get("signal_type", ""),
        "confidence": hit_rate,
        "hit_rate": hit_rate,
        "avg_return": avg_return,
        "conviction": signal_decision.get("conviction", 0),
        "entry_price_crypto": crypto_price,
        "entry_price_semi": semi_price,
        "entry_timestamp": ts,
        "eval_due": eval_due,
        "eval_status": "PENDING",
        "eval_price_crypto": None,
        "eval_price_semi": None,
        "actual_14d_return": None,
        "hit": None,
        "horizon_hours": HORIZON_HOURS,
    }


def evaluate_pending_entries(entries):
    """Scan PENDING entries past eval_due, fetch prices, compute returns."""
    now = datetime.now(timezone.utc)
    evaluated = 0

    for entry in entries:
        if entry.get("eval_status") != "PENDING":
            continue
        if entry.get("decision") != "EXECUTE":
            continue

        eval_due_str = entry.get("eval_due")
        if not eval_due_str:
            continue

        eval_due = datetime.strptime(eval_due_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if now < eval_due:
            continue

        # Time to evaluate
        crypto_symbol = entry.get("ticker")
        semi_ticker = entry.get("semi_ticker")
        entry_price = entry.get("entry_price_crypto")

        if not crypto_symbol or not entry_price:
            log(f"Cannot evaluate {entry.get('signal_id')}: missing ticker or entry price")
            continue

        eval_price = fetch_crypto_price(crypto_symbol)
        if eval_price is None:
            log(f"Price fetch failed for {crypto_symbol} — keeping PENDING")
            continue

        eval_price_semi = fetch_semi_price(semi_ticker) if semi_ticker else None

        # Compute return
        actual_return = ((eval_price - entry_price) / entry_price) * 100
        hit = actual_return > 0

        entry["eval_price_crypto"] = eval_price
        entry["eval_price_semi"] = eval_price_semi
        entry["actual_14d_return"] = round(actual_return, 2)
        entry["hit"] = hit
        entry["eval_status"] = "EVALUATED"
        entry["eval_timestamp"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        evaluated += 1

        log(f"Evaluated {entry.get('signal_id')}: {entry.get('pair')} "
            f"return={actual_return:+.2f}% hit={hit}")

    return evaluated


def compute_summary(entries):
    """Recompute all stats from scratch."""
    total = len(entries)
    no_trade_count = sum(1 for e in entries if e.get("decision") == "NO_TRADE")
    execute_count = sum(1 for e in entries if e.get("decision") == "EXECUTE")

    evaluated = [e for e in entries if e.get("eval_status") == "EVALUATED"]
    pending = [e for e in entries if e.get("eval_status") == "PENDING"]
    hits = [e for e in evaluated if e.get("hit") is True]
    misses = [e for e in evaluated if e.get("hit") is False]

    returns = [e["actual_14d_return"] for e in evaluated if e.get("actual_14d_return") is not None]
    avg_return = round(sum(returns) / len(returns), 2) if returns else None
    hit_rate = round(len(hits) / len(evaluated), 4) if evaluated else None

    # Regime breakdown
    regimes = {}
    for e in entries:
        r = e.get("regime", "UNKNOWN")
        regimes[r] = regimes.get(r, 0) + 1

    # Streak tracking
    no_trade_streak = 0
    for e in reversed(entries):
        if e.get("decision") == "NO_TRADE":
            no_trade_streak += 1
        else:
            break

    return {
        "total_entries": total,
        "no_trade_count": no_trade_count,
        "execute_count": execute_count,
        "evaluated_count": len(evaluated),
        "pending_count": len(pending),
        "hits": len(hits),
        "misses": len(misses),
        "hit_rate": hit_rate,
        "avg_return": avg_return,
        "no_trade_streak": no_trade_streak,
        "regime_distribution": regimes,
    }


def atomic_write_json(path, data):
    """Write JSON atomically via tempfile + os.replace."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    # Parse args
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline_path = os.path.join(repo_dir, "pipeline_output.json")
    ledger_path = os.path.join(repo_dir, "performance_log.json")
    api_url = os.environ.get("PF_API_URL", "http://localhost:8080")

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("--pipeline-output="):
            pipeline_path = arg.split("=", 1)[1]
        elif arg.startswith("--ledger="):
            ledger_path = arg.split("=", 1)[1]
        elif arg.startswith("--url="):
            api_url = arg.split("=", 1)[1]

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cycle_key = get_cycle_key(now)

    log(f"Cycle key: {cycle_key}")

    # Load existing ledger
    ledger = {"schema": SCHEMA, "entries": [], "summary": {}}
    if os.path.exists(ledger_path):
        try:
            with open(ledger_path) as f:
                ledger = json.load(f)
            log(f"Loaded ledger: {len(ledger.get('entries', []))} entries")
        except (json.JSONDecodeError, OSError) as e:
            log(f"Failed to load ledger, starting fresh: {e}")
            ledger = {"schema": SCHEMA, "entries": [], "summary": {}}

    entries = ledger.get("entries", [])

    # Check dedup
    if entry_exists(entries, cycle_key):
        log(f"Entry already exists for {cycle_key} — skipping")
        # Still evaluate pending entries
        evaluated = evaluate_pending_entries(entries)
        if evaluated > 0:
            ledger["entries"] = entries
            ledger["summary"] = compute_summary(entries)
            ledger["last_updated"] = ts
            atomic_write_json(ledger_path, ledger)
            log(f"Evaluated {evaluated} pending entries, wrote ledger")
        return

    # Read pipeline output
    pipeline = read_pipeline_output(pipeline_path)

    # If stale or missing, try running pipeline
    if pipeline:
        pipeline_ts = pipeline.get("timestamp", "")
        if pipeline_ts:
            try:
                pt = datetime.strptime(pipeline_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                age_sec = (now - pt).total_seconds()
                if age_sec > STALE_THRESHOLD_SEC:
                    log(f"Pipeline output is {age_sec:.0f}s old (>{STALE_THRESHOLD_SEC}s), re-running")
                    if run_pipeline(repo_dir, api_url):
                        pipeline = read_pipeline_output(pipeline_path)
            except ValueError:
                pass
    elif not pipeline:
        log("No pipeline output, attempting to run pipeline")
        if run_pipeline(repo_dir, api_url):
            pipeline = read_pipeline_output(pipeline_path)

    if not pipeline:
        log("ERROR: Could not get pipeline output — aborting")
        sys.exit(1)

    # Build entry based on decision
    overall_decision = pipeline.get("overall", {}).get("decision", "")
    scanner = pipeline.get("scanner")

    if overall_decision in ("NO_TRADE",):
        # Get regime info via fallback if scanner is None
        regime_info = None
        if scanner is None:
            regime_info = fetch_regime_fallback(api_url)
        entry = build_no_trade_entry(pipeline, cycle_key, ts, regime_info)
        entries.append(entry)
        log(f"Appended NO_TRADE entry: regime={entry['regime']}")

    elif overall_decision in ("EXECUTE", "EXECUTE_REDUCED"):
        # Build one entry per EXECUTE signal
        decisions = scanner.get("decisions", []) if scanner else []
        execute_signals = [d for d in decisions if d.get("decision") == "EXECUTE"]

        if not execute_signals:
            # Edge case: EXECUTE_REDUCED but no individual EXECUTE decisions
            entry = build_no_trade_entry(pipeline, cycle_key, ts)
            entry["decision"] = overall_decision
            entry["action"] = overall_decision
            entries.append(entry)
            log(f"Appended {overall_decision} entry (no individual signals)")
        else:
            for sig in execute_signals:
                entry = build_execute_entry(pipeline, sig, cycle_key, ts)
                # Use overall decision (could be EXECUTE_REDUCED)
                if overall_decision == "EXECUTE_REDUCED":
                    entry["note"] = f"REDUCED: {entry['note']}"
                entries.append(entry)
                log(f"Appended EXECUTE entry: {sig.get('pair')} "
                    f"crypto=${entry.get('entry_price_crypto')} "
                    f"semi=${entry.get('entry_price_semi')}")
    else:
        log(f"Unknown decision: {overall_decision}")
        sys.exit(1)

    # Evaluate pending entries past their horizon
    evaluated = evaluate_pending_entries(entries)
    if evaluated > 0:
        log(f"Evaluated {evaluated} pending entries")

    # Recompute summary
    summary = compute_summary(entries)
    ledger["entries"] = entries
    ledger["summary"] = summary
    ledger["last_updated"] = ts

    # Atomic write
    atomic_write_json(ledger_path, ledger)
    log(f"Wrote ledger: {len(entries)} entries, "
        f"{summary['no_trade_count']} NO_TRADE, "
        f"{summary['execute_count']} EXECUTE, "
        f"streak={summary['no_trade_streak']}")


if __name__ == "__main__":
    main()
