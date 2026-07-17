#!/usr/bin/env python3
"""
GEX Data Exporter — Krupp Capital Quantitative Desk
====================================================
Exports GEX data from Cloudflare Worker to various formats:
- JSON file
- CSV file  
- Telegram message (via HF Space webhook)

Usage:
    python3 gex_export.py --format json --symbols SPX,VIX
    python3 gex_export.py --format csv --output gex_data.csv
    python3 gex_export.py --format telegram --chat_id 12345
"""

import argparse
import json
import csv
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)


GEX_WORKER_URL = os.environ.get("GEX_WORKER_URL", "https://gex-collector.maxkrupp.workers.dev")
HF_WEBHOOK_URL = os.environ.get("HF_WEBHOOK_URL", "")


def fetch_gex(symbol: str) -> dict:
    """Fetch GEX data from Cloudflare Worker."""
    url = f"{GEX_WORKER_URL}/latest?symbol={symbol.upper()}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"WARNING: {symbol} HTTP {r.status_code}")
            return {}
    except Exception as e:
        print(f"WARNING: {symbol} fetch error: {e}")
        return {}


def fetch_all_symbols() -> dict:
    """Fetch all configured symbols."""
    url = f"{GEX_WORKER_URL}/symbols"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json().get("symbols", {})
    except:
        pass
    return {}


def format_gex_telegram(symbol: str, gex: dict) -> str:
    """Format GEX data for Telegram message."""
    if not gex or not gex.get("spot"):
        return f"❌ Keine GEX-Daten für {symbol}"
    
    spot = gex.get("spot", 0)
    regime = gex.get("regime", "UNKNOWN")
    net_gex = gex.get("netGexFormatted", "N/A")
    iv30 = gex.get("iv30", 0)
    change_pct = gex.get("spotChangePct", 0)
    call_wall = gex.get("callWall", {})
    put_support = gex.get("putSupport", {})
    hvl = gex.get("hvl", 0)
    chain_source = gex.get("chainSource", "unknown")
    front_expiry = gex.get("frontExpiry", "N/A")
    dte = gex.get("dte", "?")
    strike_count = gex.get("strikeCount", 0)
    
    sign = "+" if change_pct >= 0 else ""
    regime_emoji = "🟢" if "POSITIVE" in regime else "🔴" if "NEGATIVE" in regime else "⚪"
    
    lines = [
        f"*{symbol}* | GEX Report",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Spot: *{spot:.2f}* {sign}{change_pct:.2f}%",
        f"📊 IV30: {iv30:.1f}% | Regime: {regime_emoji} {regime}",
        f"📈 Net GEX: *{net_gex}*",
        f"",
        f"🔵 Call Wall: *{call_wall.get('strike', 'N/A')}* ({call_wall.get('gex', 'N/A')})",
        f"🔴 Put Support: *{put_support.get('strike', 'N/A')}* ({put_support.get('gex', 'N/A')})",
        f"⚖️ HVL: *{hvl}*",
        f"",
        f"📅 Expiry: {front_expiry} (DTE: {dte}) | Strikes: {strike_count}",
        f"📡 Source: {chain_source}",
    ]
    
    top_calls = gex.get("topCallStrikes", [])[:5]
    top_puts = gex.get("topPutStrikes", [])[:5]
    
    if top_calls:
        lines.append(f"\n🔵 Top Call GEX:")
        for s in top_calls:
            lines.append(f"  {s['strike']}: {s['gex']} (OI: {s['oi']:,})")
    
    if top_puts:
        lines.append(f"\n🔴 Top Put GEX:")
        for s in top_puts:
            lines.append(f"  {s['strike']}: {s['gex']} (OI: {s['oi']:,})")
    
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"_Krupp Capital Quantitative Desk_")
    lines.append(f"_Dient der Information. Keine Anlageberatung._")
    
    return "\n".join(lines)


def export_json(symbols: list, output_dir: str = "."):
    """Export GEX data as JSON files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    results = {}
    for sym in symbols:
        gex = fetch_gex(sym)
        if gex:
            results[sym] = gex
            filepath = out / f"gex_{sym.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filepath, "w") as f:
                json.dump(gex, f, indent=2)
            print(f"✅ {sym}: {filepath}")
        else:
            print(f"❌ {sym}: no data")
    
    # Combined file
    if results:
        combined_path = out / f"gex_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(combined_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"📦 Combined: {combined_path}")
    
    return results


def export_csv(symbols: list, output: str = "gex_data.csv"):
    """Export GEX data as CSV."""
    rows = []
    for sym in symbols:
        gex = fetch_gex(sym)
        if gex:
            rows.append({
                "timestamp": gex.get("timestamp", ""),
                "symbol": sym,
                "spot": gex.get("spot", 0),
                "iv30": gex.get("iv30", 0),
                "change_pct": gex.get("spotChangePct", 0),
                "regime": gex.get("regime", ""),
                "net_gex": gex.get("netGexFormatted", ""),
                "call_wall_strike": gex.get("callWall", {}).get("strike", 0),
                "put_support_strike": gex.get("putSupport", {}).get("strike", 0),
                "hvl": gex.get("hvl", 0),
                "chain_source": gex.get("chainSource", ""),
                "strike_count": gex.get("strikeCount", 0),
                "front_expiry": gex.get("frontExpiry", ""),
                "dte": gex.get("dte", ""),
            })
    
    if rows:
        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"✅ CSV: {output} ({len(rows)} symbols)")
    else:
        print("❌ No data to export")


def send_telegram(chat_id: str, symbols: list):
    """Send GEX report via HF Space webhook."""
    if not HF_WEBHOOK_URL:
        print("ERROR: HF_WEBHOOK_URL not set")
        return False
    
    for sym in symbols:
        gex = fetch_gex(sym)
        if gex:
            message = format_gex_telegram(sym, gex)
            try:
                r = requests.post(HF_WEBHOOK_URL, json={
                    "type": "gex_report",
                    "chat_id": chat_id,
                    "message": message,
                    "symbol": sym,
                }, timeout=15)
                if r.status_code == 200:
                    print(f"✅ {sym}: sent to Telegram")
                else:
                    print(f"❌ {sym}: HTTP {r.status_code}")
            except Exception as e:
                print(f"❌ {sym}: {e}")
    
    return True


def main():
    parser = argparse.ArgumentParser(description="GEX Data Exporter — Krupp Capital")
    parser.add_argument("--format", choices=["json", "csv", "telegram"], required=True)
    parser.add_argument("--symbols", default="SPX,VIX", help="Comma-separated symbols")
    parser.add_argument("--output", default=".", help="Output directory/file")
    parser.add_argument("--chat_id", default="", help="Telegram chat_id for telegram format")
    args = parser.parse_args()
    
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    print(f"=== GEX Data Exporter ===")
    print(f"Worker: {GEX_WORKER_URL}")
    print(f"Symbols: {symbols}")
    print(f"Format: {args.format}")
    print("")
    
    if args.format == "json":
        export_json(symbols, args.output)
    elif args.format == "csv":
        export_csv(symbols, args.output)
    elif args.format == "telegram":
        if not args.chat_id:
            print("ERROR: --chat_id required for telegram format")
            sys.exit(1)
        send_telegram(args.chat_id, symbols)
    
    print("\nDone.")


if __name__ == "__main__":
    main()
