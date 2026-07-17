"""
charts_webhook.py — Krupp Capital Dark Institutional Chart Webhook
=================================================================

Flask HTTP service that renders GEX charts using the dashboard's data pipeline
(``data_fetcher`` → ``gex_calculator`` → ``chart_generator.render_chart``).

Endpoints
---------
  GET  /health                 → 200 {"ok": true}                       (liveness)
  GET  /                       → 200 JSON listing endpoints              (introspection)
  GET  /chart/<symbol>         → image/png                              (one chart pull)
  POST /chart   body: {
          "symbol": "/ES",                               # required
          "raw":    { "data": {...} },                    # optional pre-fetched CBOE JSON (NYI)
          "spot":   754.81,                               # optional override
        }
                                → image/png

Deployment target: Hugging Face Space (already wired with HF_WEBHOOK_URL).
Local test: ``python charts_webhook.py`` then ``curl http://localhost:7860/chart/SPY -o spy.png``.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

from flask import Flask, Response, request, jsonify

# Add repo root to sys.path so cboe_menthorq_dashboard.* imports work
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from cboe_menthorq_dashboard.data_fetcher import LiveOptionsFetcher
from cboe_menthorq_dashboard.gex_calculator import GEXCalculator
from cboe_menthorq_dashboard.chart_generator import render_chart

app = Flask(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("charts_webhook")


# ================================================================
# CORS — open by default; tighten via env vars in production if needed
# ================================================================
@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "krupp-capital-charts", "version": "v1"})


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "Krupp Capital Dark Institutional GEX Charts",
        "endpoints": {
            "GET /health": "liveness probe",
            "GET /chart/<symbol>": "render dark theme PNG for symbol",
            "POST /chart": "render PNG; body {symbol*, raw?, spot?, max_dte?}",
        },
        "supported_symbols": ["SPX", "SPY", "QQQ", "VIX", "GLD", "SLV", "USO",
                              "NDX", "RUT", "IWM"],
        "theme": {
            "background": "#0a0a0a",
            "primary_accent": "#d4af37 (gold)",
            "calls": "#26d97f (green)",
            "puts": "#e63946 (red)",
            "hashtag": "#crafted by Krupp Capital  (centered at bottom)",
        },
    })


@app.route("/chart/<symbol>", methods=["GET"])
def chart_get(symbol: str):
    """Render the chart for `symbol` using live CBOE data."""
    return _render(symbol=symbol, spot=None)


@app.route("/chart", methods=["POST", "OPTIONS"])
def chart_post():
    """Render with optional spot override."""
    if request.method == "OPTIONS":
        return Response("", status=204)
    body = request.get_json(force=True, silent=True) or {}
    symbol = body.get("symbol") or request.args.get("symbol")
    spot = body.get("spot") or None
    return _render(symbol=symbol, spot=spot)


def _render(symbol: str, spot) -> Response:
    if not symbol:
        return jsonify({"error": "missing required field 'symbol'"}), 400
    symbol = str(symbol).upper()
    try:
        # Dashboard pipeline: fetch → compute → render
        fetcher = LiveOptionsFetcher(symbol)
        chain = fetcher.fetch_all_chains()
        if spot is not None:
            spot = float(spot)
        else:
            spot = fetcher.spot_price()
        spot = float(spot)

        gex = GEXCalculator(chain, spot)
        by_strike = gex.gex_by_strike()

        from datetime import datetime
        png = render_chart(
            symbol=symbol,
            by_strike=by_strike,
            spot=spot,
            date_label=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )
        log.info("rendered %s (%d KiB)", symbol, len(png) // 1024)
        return Response(png, mimetype="image/png",
                        headers={"Cache-Control": "public, max-age=60"})
    except Exception as e:
        log.error("render failed for %s: %s\n%s", symbol, e, traceback.format_exc())
        return jsonify({"error": str(e), "symbol": symbol}), 500


# ================================================================
# Entrypoint — `python charts_webhook.py` starts the dev server.
# Production deployment on Hugging Face uses gunicorn or uvicorn per HF docs.
# ================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))  # HF Spaces default = 7860
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
