#!/usr/bin/env python3
"""
GEX Data Sender — Push GEX data to HF Space / GitHub
=====================================================
Sends GEX data from local machine to:
1. HF Space (via webhook)
2. GitHub Gist (via API)
3. Local file (JSON/CSV)

Usage:
    python3 gex_send.py --target hf_space --symbols SPX,VIX
    python3 gex_send.py --target github_gist --symbols SPX
    python3 gex_send.py --target file --output ./gex_data/
"""

import argparse
import json
import csv
import sys
import os
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed")
    sys.exit(1)


GEX_WORKER_URL = os.environ.get("GEX_WORKER_URL", "https://gex-collector.maxkrupp.workers.dev")
HF_SPACE_URL = os.environ.get("HF_SPACE_URL", "https://maxkru92-hermes-neu-volatility-vince.hf.space")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def fetch_gex(symbol: str) -> dict:
    """Fetch GEX data from Cloudflare Worker."""
    try:
        r = requests.get(f"{GEX_WORKER_URL}/latest?symbol={symbol.upper()}", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"WARNING: {symbol}: {e}")
    return {}


def send_to_hf_space(symbols: list):
    """Send GEX data to HF Space for Telegram delivery."""
    results = {}
    for sym in symbols:
        gex = fetch_gex(sym)
        if gex:
            # Push to HF Space /gex endpoint
            try:
                r = requests.post(f"{HF_SPACE_URL}/gex_update", json={
                    "symbol": sym,
                    "data": gex,
                    "source": "gex-collector",
                }, timeout=15)
                if r.status_code == 200:
                    print(f"✅ {sym}: pushed to HF Space")
                    results[sym] = gex
                else:
                    print(f"❌ {sym}: HF Space HTTP {r.status_code}")
            except Exception as e:
                print(f"❌ {sym}: {e}")
    return results


def send_to_github_gist(symbols: list):
    """Create/update a GitHub Gist with GEX data."""
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set")
        return
    
    results = {}
    for sym in symbols:
        gex = fetch_gex(sym)
        if gex:
            results[sym] = gex
    
    if not results:
        print("No data to send")
        return
    
    # Create gist
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    filename = f"gex_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    try:
        r = requests.post(
            "https://api.github.com/gists",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={
                "description": f"GEX Data — {timestamp} | Krupp Capital",
                "public": False,
                "files": {
                    filename: {
                        "content": json.dumps(results, indent=2)
                    }
                }
            },
            timeout=15
        )
        if r.status_code == 201:
            gist_url = r.json().get("html_url", "")
            print(f"✅ Gist created: {gist_url}")
        else:
            print(f"❌ Gist failed: HTTP {r.status_code}")
    except Exception as e:
        print(f"❌ Gist error: {e}")


def save_to_file(symbols: list, output_dir: str = "./gex_data"):
    """Save GEX data to local files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    results = {}
    for sym in symbols:
        gex = fetch_gex(sym)
        if gex:
            results[sym] = gex
            # Individual file
            filepath = out / f"gex_{sym.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filepath, "w") as f:
                json.dump(gex, f, indent=2)
            print(f"✅ {sym}: {filepath}")
    
    # Combined
    if results:
        combined = out / f"gex_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(combined, "w") as f:
            json.dump(results, f, indent=2)
        print(f"📦 Combined: {combined}")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="GEX Data Sender — Krupp Capital")
    parser.add_argument("--target", choices=["hf_space", "github_gist", "file"], required=True)
    parser.add_argument("--symbols", default="SPX,VIX")
    parser.add_argument("--output", default="./gex_data")
    args = parser.parse_args()
    
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    print(f"=== GEX Data Sender ===")
    print(f"Target: {args.target}")
    print(f"Symbols: {symbols}")
    print(f"Worker: {GEX_WORKER_URL}")
    print("")
    
    if args.target == "hf_space":
        send_to_hf_space(symbols)
    elif args.target == "github_gist":
        send_to_github_gist(symbols)
    elif args.target == "file":
        save_to_file(symbols, args.output)
    
    print("\nDone.")


if __name__ == "__main__":
    main()
