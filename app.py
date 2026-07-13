"""
MK Quant Monitor — Institutional Trading Terminal v7.0
Volatility Vince Edition
Tabs: Markets Overview | SPX & VIX Analytics | Volatility Vince | MarketGuardian Pro | Crypto Ultra | Insider Trades | Settings
"""
import os, sys, json, importlib.util, pathlib, logging
from datetime import datetime, timedelta, date
from collections import defaultdict
from typing import Optional, Dict, Any, List, Tuple

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Rectangle, Wedge
from matplotlib.collections import PatchCollection

import requests
from scipy import stats as _scipy_stats

# -- CBOE Adapter --
CBOE_BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options"
CBOE_SYMBOLS = {"SPX":"SPX","VIX":"VIX","NDX":"NDX","RUT":"RUT","DJX":"DJX","SPY":"SPY","QQQ":"QQQ"}
_CBOE_HEADERS = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}

def _cboe_fetch_chain(symbol):
    sym = CBOE_SYMBOLS.get(symbol.upper(), symbol.upper())
    url = f"{CBOE_BASE_URL}/{sym}.json"
    try:
        r = requests.get(url, headers=_CBOE_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "data" not in data: return None
        return data
    except: return None

def _cboe_parse_chain(raw_data):
    if not raw_data or "data" not in raw_data:
        return {"spot":0,"records":[],"calls_df":pd.DataFrame(),"puts_df":pd.DataFrame(),"expirations":[],"timestamp":""}
    data = raw_data["data"]
    spot = float(data.get("current_price", 0))
    options = data.get("options", [])
    ts = datetime.utcnow().isoformat()
    if not options:
        return {"spot":spot,"records":[],"calls_df":pd.DataFrame(),"puts_df":pd.DataFrame(),"expirations":[],"timestamp":ts}
    records, calls_r, puts_r = [], [], []
    for opt in options:
        try:
            rec = {
                "strike": float(opt.get("strike",0)),
                "expiry": str(opt.get("expiration","")),
                "option_type": str(opt.get("option_type","")).upper(),
                "iv": float(opt.get("iv",0) or 0),
                "delta": float(opt.get("delta",0) or 0),
                "gamma": float(opt.get("gamma",0) or 0),
                "theta": float(opt.get("theta",0) or 0),
                "vega": float(opt.get("vega",0) or 0),
                "open_interest": int(opt.get("open_interest",0) or 0),
                "bid": float(opt.get("bid",0) or 0),
                "ask": float(opt.get("ask",0) or 0),
                "last": float(opt.get("last_price",0) or 0),
                "volume": int(opt.get("volume",0) or 0),
            }
            if rec["strike"] <= 0: continue
            records.append(rec)
            if rec["option_type"] == "C": calls_r.append(rec)
            elif rec["option_type"] == "P": puts_r.append(rec)
        except: continue
    expirations = sorted(set(r["expiry"] for r in records if r["expiry"]))
    return {"spot":spot,"records":records,"calls_df":pd.DataFrame(calls_r),"puts_df":pd.DataFrame(puts_r),"expirations":expirations,"timestamp":ts,"source":"cboe_live"}

def cboe_fetch_spx_chain():
    raw = _cboe_fetch_chain("SPX")
    return _cboe_parse_chain(raw) if raw else None

# -- GEX Calculator --
def _safe_float(v, default=0.0):
    try:
        if v is None: return default
        f = float(v)
        return f if np.isfinite(f) else default
    except: return default

def _safe_int(v, default=0):
    try:
        if v is None: return default
        return int(float(v))
    except: return default

def compute_gex_plus(records, spot):
    if not records or not spot or spot <= 0: return 0.0
    spot = float(spot)
    total, disagreements = 0.0, 0
    for s in records:
        net_oi = _safe_int(s.get("oi_call")) - _safe_int(s.get("oi_put"))
        gamma = (_safe_float(s.get("gamma_call")) + _safe_float(s.get("gamma_put"))) / 2.0
        total += net_oi * gamma * spot * spot / 100.0
        iv_c = _safe_float(s.get("iv_call", 0.001))
        iv_p = _safe_float(s.get("iv_put", 0.001))
        if iv_p > iv_c * 1.02 and iv_c > 0: disagreements += 1
    rate = disagreements / len(records) if records else 0.0
    return total * max(0.0, 1.0 - 2.0 * rate)

def compute_vanna_exposure(records, spot):
    if not records or not spot or spot <= 0: return 0.0
    spot = float(spot)
    total = 0.0
    for s in records:
        vanna_c = _safe_float(s.get("vanna_call"))
        vanna_p = _safe_float(s.get("vanna_put"))
        if vanna_c == 0 and vanna_p == 0:
            gamma_c = _safe_float(s.get("gamma_call"))
            gamma_p = _safe_float(s.get("gamma_put"))
            iv_c = _safe_float(s.get("iv_call", 0.20))
            iv_p = _safe_float(s.get("iv_put", 0.20))
            strike = _safe_float(s.get("strike"))
            if iv_c > 0 and strike > 0:
                d1 = (np.log(spot/strike) + 0.5*iv_c**2) / iv_c
                vanna_c = -gamma_c * d1 / iv_c
            if iv_p > 0 and strike > 0:
                d1 = (np.log(spot/strike) + 0.5*iv_p**2) / iv_p
                vanna_p = -gamma_p * d1 / iv_p
        oi_c = _safe_int(s.get("oi_call"))
        oi_p = _safe_int(s.get("oi_put"))
        total += vanna_c * oi_c * spot + vanna_p * oi_p * spot
    return total

def find_zero_gamma(records, spot, max_range_pct=25.0, step=0.1):
    if not records or not spot or spot <= 0: return spot
    gex_at = compute_gex_plus(records, spot)
    if gex_at == 0: return spot
    sign_at = np.sign(gex_at)
    for pct in np.arange(0.0, max_range_pct+step, step):
        test = spot * (1+pct/100)
        scaled = [{**s, "gamma_call":_safe_float(s.get("gamma_call"))*spot/test, "gamma_put":_safe_float(s.get("gamma_put"))*spot/test} for s in records]
        if np.sign(compute_gex_plus(scaled, test)) != sign_at: return test
    for pct in np.arange(0.0, -max_range_pct-step, -step):
        test = spot * (1+pct/100)
        scaled = [{**s, "gamma_call":_safe_float(s.get("gamma_call"))*spot/test, "gamma_put":_safe_float(s.get("gamma_put"))*spot/test} for s in records]
        if np.sign(compute_gex_plus(scaled, test)) != sign_at: return test
    return spot * 1.05

def compute_crash_profile(records, spot, range_pct=(-15.0, 10.0), step=0.25):
    if not records or not spot or spot <= 0: return []
    profile = []
    for pct in np.arange(range_pct[0], range_pct[1]+step, step):
        test = spot * (1+pct/100)
        scaled = [{**s, "gamma_call":_safe_float(s.get("gamma_call"))*spot/test, "gamma_put":_safe_float(s.get("gamma_put"))*spot/test} for s in records]
        profile.append({"spot_pct":round(float(pct),2),"spx":round(test,1),"gex_plus":compute_gex_plus(scaled,test)})
    return profile

def compute_chain_greeks(chain_records, spot, r=0.05, dte=None):
    if not chain_records: return pd.DataFrame()
    rows = []
    for rec in chain_records:
        strike = float(rec.get("strike",0))
        if strike <= 0: continue
        T = dte/365.0 if dte else 30.0/365.0
        iv_c = float(rec.get("iv_call", 0.20))
        iv_p = float(rec.get("iv_put", 0.20))
        oi_c = int(rec.get("oi_call", 0))
        oi_p = int(rec.get("oi_put", 0))
        if iv_c > 0 and T > 0:
            d1 = (np.log(spot/strike) + (r+0.5*iv_c**2)*T) / (iv_c*np.sqrt(T))
            d2 = d1 - iv_c*np.sqrt(T)
            delta_c = float(_scipy_stats.norm.cdf(d1))
            gamma_c = float(_scipy_stats.norm.pdf(d1) / (spot*iv_c*np.sqrt(T)))
            theta_c = float((-(spot*_scipy_stats.norm.pdf(d1)*iv_c)/(2*np.sqrt(T)) - r*strike*np.exp(-r*T)*_scipy_stats.norm.cdf(d2)) / 365.0)
            vega_c = float(spot * _scipy_stats.norm.pdf(d1) * np.sqrt(T) * 0.01)
        else:
            delta_c = gamma_c = theta_c = vega_c = 0.0
        if iv_p > 0 and T > 0:
            d1 = (np.log(spot/strike) + (r+0.5*iv_p**2)*T) / (iv_p*np.sqrt(T))
            d2 = d1 - iv_p*np.sqrt(T)
            delta_p = float(_scipy_stats.norm.cdf(d1) - 1.0)
            gamma_p = float(_scipy_stats.norm.pdf(d1) / (spot*iv_p*np.sqrt(T)))
            theta_p = float((-(spot*_scipy_stats.norm.pdf(d1)*iv_p)/(2*np.sqrt(T)) + r*strike*np.exp(-r*T)*_scipy_stats.norm.cdf(-d2)) / 365.0)
            vega_p = float(spot * _scipy_stats.norm.pdf(d1) * np.sqrt(T) * 0.01)
        else:
            delta_p = gamma_p = theta_p = vega_p = 0.0
        rows.append({"strike":strike,"T":T,"dte":int(T*365),"iv_call":iv_c,"iv_put":iv_p,"delta_call":delta_c,"delta_put":delta_p,"gamma_call":gamma_c,"gamma_put":gamma_p,"theta_call":theta_c,"theta_put":theta_p,"vega_call":vega_c,"vega_put":vega_p,"oi_call":oi_c,"oi_put":oi_p})
    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)

def realized_vol(prices, window=20, annualize=True):
    if prices is None or len(prices) < 2: return pd.Series(dtype=float)
    lr = np.log(prices / prices.shift(1))
    vol = lr.rolling(window=window).std()
    if annualize: vol = vol * np.sqrt(252)
    return vol

def iv_term_structure(chain_records, spot):
    if not chain_records: return pd.DataFrame()
    by_exp = {}
    for rec in chain_records:
        exp = rec.get("expiry","unknown")
        by_exp.setdefault(exp, []).append(rec)
    rows = []
    for exp, recs in sorted(by_exp.items()):
        atm = sorted(recs, key=lambda r: abs(r.get("strike",0)-spot))[:4]
        iv_c = np.mean([r.get("iv_call",0) for r in atm if r.get("iv_call",0)>0]) if any(r.get("iv_call",0)>0 for r in atm) else 0
        iv_p = np.mean([r.get("iv_put",0) for r in atm if r.get("iv_put",0)>0]) if any(r.get("iv_put",0)>0 for r in atm) else 0
        try:
            dte = max(0, (pd.to_datetime(exp) - pd.Timestamp.now()).days)
        except: dte = 0
        rows.append({"expiry":exp,"dte":dte,"atm_iv_call":iv_c,"atm_iv_put":iv_p,"avg_iv":(iv_c+iv_p)/2.0,"skew":iv_p-iv_c})
    return pd.DataFrame(rows).sort_values("dte").reset_index(drop=True)

def compute_iv_skew(chain_records, spot):
    if not chain_records: return {"raw_skew":0,"atm_skew":0,"wing_skew":0,"skew_slope":0}
    data = []
    for rec in chain_records:
        strike = rec.get("strike",0)
        if strike <= 0: continue
        iv_c = rec.get("iv_call",0)
        iv_p = rec.get("iv_put",0)
        moneyness = (strike/spot-1)*100
        data.append({"strike":strike,"moneyness":moneyness,"iv_call":iv_c,"iv_put":iv_p,"raw_skew":iv_p-iv_c})
    if not data: return {"raw_skew":0,"atm_skew":0,"wing_skew":0,"skew_slope":0}
    df = pd.DataFrame(data)
    raw_skew = df["raw_skew"].mean()
    atm_skew = df[df["moneyness"].abs()<1.0]["raw_skew"].mean() if len(df[df["moneyness"].abs()<1.0])>0 else raw_skew
    otm_puts = df[df["moneyness"]<-2]
    otm_calls = df[df["moneyness"]>2]
    wing_skew = (otm_puts["iv_put"].mean() - otm_calls["iv_call"].mean()) if len(otm_puts)>0 and len(otm_calls)>0 else 0
    return {"raw_skew":raw_skew,"atm_skew":atm_skew,"wing_skew":wing_skew,"skew_slope":0}

def vol_regime(current_iv, rv_20d, rv_60d, iv_rank, iv_pct, term_slope):
    if iv_rank > 75: regime, color = "High Vol", "red"
    elif iv_rank > 50: regime, color = "Above Normal", "orange"
    elif iv_rank > 25: regime, color = "Below Normal", "yellow"
    else: regime, color = "Low Vol", "green"
    vrp = current_iv - rv_20d
    if term_slope > 2: term_sig = "Strong Contango"
    elif term_slope > 0.5: term_sig = "Contango"
    elif term_slope > -0.5: term_sig = "Flat"
    elif term_slope > -2: term_sig = "Backwardation"
    else: term_sig = "Strong Backwardation"
    signals = []
    if iv_rank > 80: signals.append("IV Elevated - Consider selling vol")
    elif iv_rank < 20: signals.append("IV Compressed - Consider buying vol")
    if vrp > 5: signals.append("High Vol Risk Premium")
    if "Backwardation" in term_sig: signals.append("Term Structure Inverted - Stress")
    return {"regime":regime,"regime_color":color,"iv_rank":iv_rank,"vrp":vrp,"term_signal":term_sig,"signals":signals}

def _compute_heatmap_inline(strikes_list, spot, spot_range=(10,-15), iv_range=(-10,40), n_spot=40, n_iv=50):
    spot_steps = np.linspace(spot_range[0], spot_range[1], n_spot)
    iv_steps = np.linspace(iv_range[0], iv_range[1], n_iv)
    grid = np.zeros((n_spot, n_iv))
    for i, sp in enumerate(spot_steps):
        test_spot = spot * (1 + sp/100)
        scaled = [{**s, "gamma_call": s.get("gamma_call",0)*spot/test_spot, "gamma_put": s.get("gamma_put",0)*spot/test_spot} for s in strikes_list]
        for j, iv_shock in enumerate(iv_steps):
            iv_adj = [{**s, "iv_call": max(0.001, s.get("iv_call",0)+iv_shock/100), "iv_put": max(0.001, s.get("iv_put",0)+iv_shock/100)} for s in scaled]
            grid[i,j] = compute_gex_plus(iv_adj, test_spot)
    max_abs = np.abs(grid).max()
    if max_abs > 0: grid = grid / max_abs
    return {"spot_range": list(spot_range), "iv_range": list(iv_range), "values": grid.tolist()}

def _compute_bl_forecast_inline(strikes_list, spot, dte=30, target_dte_1d=1, target_dte_1w=5):
    from scipy import stats
    atm = min(strikes_list, key=lambda s: abs(s.get("strike",0) - spot)) if strikes_list else {"iv_call":0.18,"iv_put":0.19}
    atm_iv = (atm.get("iv_call",0.18) + atm.get("iv_put",0.19)) / 2
    down = min(strikes_list, key=lambda s: abs(s.get("strike",0) - spot*0.95)) if strikes_list else {"iv_put":0.20}
    up = min(strikes_list, key=lambda s: abs(s.get("strike",0) - spot*1.05)) if strikes_list else {"iv_call":0.16}
    skew = (down.get("iv_put",0.20) - up.get("iv_call",0.16)) * 10
    def make_fc(target):
        sig = atm_iv * np.sqrt(target/252)
        fwd = spot
        z_map = {"p5":-1.645,"p25":-0.674,"p50":0.0,"p75":0.674,"p95":1.645}
        def cf(z): return z + (skew/6)*(z**2-1)
        pctiles = {k: round(fwd*np.exp(cf(z)*sig-0.5*sig**2),1) for k,z in z_map.items()}
        levels = sorted({round(spot), round(spot*0.95), round(spot*1.05)})
        table = []
        for lvl in levels:
            if sig > 0:
                z = (np.log(lvl/fwd)+0.5*sig**2)/sig
                p_below = round(float(stats.norm.cdf(z))*100,1)
            else: p_below = 100.0 if lvl >= spot else 0.0
            table.append({"level":lvl,"p_below":p_below,"p_above":round(100-p_below,1)})
        return {"sigma_pts":round(sig*spot),"sigma_pct":round(sig*100,2),"forward":round(fwd,1),"p5":pctiles["p5"],"p25":pctiles["p25"],"median":pctiles["p50"],"p75":pctiles["p75"],"p95":pctiles["p95"],"range_90":[pctiles["p5"],pctiles["p95"]],"table":table}
    return {"one_day": make_fc(target_dte_1d), "one_week": make_fc(target_dte_1w)}

# =============================================================================
# VOLATILITY VINTAGE INTELLIGENCE FUNCTIONS
# =============================================================================

def compute_vvix_percentile(vvix_series, lookback=252):
    """Compute VVIX percentile/rank over lookback window."""
    if vvix_series is None or len(vvix_series) < 20:
        return None, None, None
    current = vvix_series.iloc[-1]
    window = vvix_series.tail(min(lookback, len(vvix_series)))
    percentile = (window < current).sum() / len(window) * 100
    rank = percentile
    return float(current), float(percentile), float(rank)

def compute_vix_vvix_ratio(vix_series, vvix_series):
    """VIX/VVIX ratio — mean reversion signal."""
    if vix_series is None or vvix_series is None:
        return None, None, None
    min_len = min(len(vix_series), len(vvix_series))
    if min_len < 2:
        return None, None, None
    vix = vix_series.tail(min_len)
    vvix = vvix_series.tail(min_len)
    ratio = vix.values / vvix.values
    current_ratio = ratio[-1]
    # Z-score of ratio over 60 days
    if len(ratio) >= 60:
        window = ratio[-60:]
        z = (current_ratio - np.mean(window)) / (np.std(window) + 1e-9)
    else:
        z = 0.0
    return float(current_ratio), float(z), ratio

def compute_vol_regime_detection(vix_series, vvix_series, spx_series):
    """
    HMM-inspired regime detection using VIX, VVIX, and SPX returns.
    Returns current regime, probabilities, and transition matrix.
    """
    regimes = ["Low Vol", "Normal", "Elevated", "Crisis"]
    regime_colors = {"Low Vol": "#00ff88", "Normal": "#00d4ff", "Elevated": "#ffaa00", "Crisis": "#ff4444"}

    if vix_series is None or len(vix_series) < 60:
        return {"regime": "Unknown", "probabilities": {}, "transition_matrix": {},
                "regime_color": "#8899aa", "history": []}

    vix = vix_series.tail(252).values
    current_vix = vix[-1]

    # Compute SPX returns if available
    if spx_series is not None and len(spx_series) > 1:
        spx_ret = spx_series.pct_change().tail(252).dropna().values
        vol_spx = np.std(spx_ret[-20:]) * np.sqrt(252) * 100 if len(spx_ret) >= 20 else 15.0
    else:
        vol_spx = 15.0

    # VVIX level
    current_vvix = vvix_series.iloc[-1] if vvix_series is not None and len(vvix_series) > 0 else 85.0

    # Regime thresholds based on VIX percentiles
    vix_p25 = np.percentile(vix, 25)
    vix_p50 = np.percentile(vix, 50)
    vix_p75 = np.percentile(vix, 75)

    # Compute regime probabilities using Gaussian likelihoods
    regime_params = {
        "Low Vol":    {"vix_mean": vix_p25 * 0.7,  "vix_std": 3.0,  "vvix_mean": 70, "vvix_std": 8},
        "Normal":     {"vix_mean": vix_p50,         "vix_std": 4.0,  "vvix_mean": 82, "vvix_std": 10},
        "Elevated":   {"vix_mean": vix_p75,         "vix_std": 5.0,  "vvix_mean": 95, "vvix_std": 12},
        "Crisis":     {"vix_mean": vix_p75 * 1.5,   "vix_std": 8.0,  "vvix_mean": 115, "vvix_std": 18},
    }

    log_probs = {}
    for reg, params in regime_params.items():
        lp_vix = -0.5 * ((current_vix - params["vix_mean"]) / params["vix_std"])**2
        lp_vvix = -0.5 * ((current_vvix - params["vvix_mean"]) / params["vvix_std"])**2
        log_probs[reg] = lp_vix + lp_vvix

    # Convert to probabilities
    max_lp = max(log_probs.values())
    probs = {r: np.exp(lp - max_lp) for r, lp in log_probs.items()}
    total = sum(probs.values())
    probs = {r: p/total for r, p in probs.items()}

    current_regime = max(probs, key=probs.get)

    # Build transition matrix from historical data
    trans_matrix = compute_regime_transition_matrix(vix, vvix_series)

    # Regime history (last 30 days)
    hist = []
    for i in range(max(0, len(vix)-30), len(vix)):
        day_probs = {}
        for reg, params in regime_params.items():
            lp = -0.5 * ((vix[i] - params["vix_mean"]) / params["vix_std"])**2
            day_probs[reg] = lp
        max_lp = max(day_probs.values())
        day_probs = {r: np.exp(lp - max_lp) for r, lp in day_probs.items()}
        t = sum(day_probs.values())
        day_probs = {r: round(p/t*100, 1) for r, p in day_probs.items()}
        hist.append({"idx": i, "vix": vix[i], "regime": max(day_probs, key=day_probs.get), **day_probs})

    return {
        "regime": current_regime,
        "probabilities": {r: round(p*100, 1) for r, p in probs.items()},
        "transition_matrix": trans_matrix,
        "regime_color": regime_colors.get(current_regime, "#8899aa"),
        "history": hist,
        "vix_percentile": float((vix < current_vix).sum() / len(vix) * 100),
    }

def compute_regime_transition_matrix(vix_values, vvix_series):
    """Compute empirical regime transition matrix from VIX history."""
    if vix_values is None or len(vix_values) < 60:
        return {}
    vix = vix_values
    p25, p50, p75 = np.percentile(vix, 25), np.percentile(vix, 50), np.percentile(vix, 75)
    labels = ["Low Vol", "Normal", "Elevated", "Crisis"]

    def classify(v):
        if v < p25: return 0
        elif v < p50: return 1
        elif v < p75: return 2
        else: return 3

    states = [classify(v) for v in vix]
    counts = np.zeros((4, 4))
    for i in range(len(states)-1):
        counts[states[i]][states[i+1]] += 1

    matrix = {}
    for i, from_reg in enumerate(labels):
        matrix[from_reg] = {}
        row_total = counts[i].sum()
        for j, to_reg in enumerate(labels):
            matrix[from_reg][to_reg] = round(float(counts[i][j] / row_total * 100), 1) if row_total > 0 else 0.0
    return matrix

def compute_vol_risk_premium(vix_series, spx_series, windows=[20, 60, 120]):
    """
    Vol Risk Premium = IV (VIX) - RV (realized vol of SPX).
    Returns VRP for multiple tenors with Z-scores.
    """
    if vix_series is None or spx_series is None:
        return {}

    results = {}
    vix = vix_series.dropna()
    spx = spx_series.dropna()

    for w in windows:
        label = f"{w}d"
        if len(spx) < w + 5:
            continue
        rv = realized_vol(spx, window=w)
        if rv is None or len(rv) < 2:
            continue
        # Align VIX and RV
        common_idx = vix.index.intersection(rv.index)
        if len(common_idx) < 20:
            continue
        vix_aligned = vix.loc[common_idx]
        rv_aligned = rv.loc[common_idx]
        vrp = vix_aligned - rv_aligned
        current_vrp = float(vrp.iloc[-1])

        # Z-score over available history
        vrp_mean = vrp.rolling(min(60, len(vrp)), min_periods=10).mean().iloc[-1]
        vrp_std = vrp.rolling(min(60, len(vrp)), min_periods=10).std().iloc[-1]
        vrp_z = (current_vrp - vrp_mean) / (vrp_std + 1e-9) if vrp_std > 0 else 0.0

        results[label] = {
            "vix": float(vix_aligned.iloc[-1]),
            "rv": float(rv_aligned.iloc[-1]),
            "vrp": current_vrp,
            "vrp_zscore": float(vrp_z),
            "vrp_mean": float(vrp_mean),
            "vrp_history": vrp,
        }
    return results

def compute_vix_fear_gauge(vix_val, vvix_val, vix_vvix_ratio, vrp_val):
    """
    Normalized fear index combining VIX, VVIX, VIX/VVIX ratio, VRP.
    0-100 scale with color zones.
    """
    components = {}

    # VIX component (0-40 points) — VIX typically 10-80
    if vix_val is not None:
        vix_score = min(40, max(0, (vix_val - 10) / 70 * 40))
    else:
        vix_score = 20
    components["VIX"] = vix_score

    # VVIX component (0-25 points) — VVIX typically 60-150
    if vvix_val is not None:
        vvix_score = min(25, max(0, (vvix_val - 60) / 90 * 25))
    else:
        vvix_score = 12.5
    components["VVIX"] = vvix_score

    # VIX/VVIX ratio component (0-15 points) — ratio typically 0.08-0.25
    if vix_vvix_ratio is not None:
        ratio_score = min(15, max(0, (vix_vvix_ratio - 0.08) / 0.17 * 15))
    else:
        ratio_score = 7.5
    components["VIX/VVIX"] = ratio_score

    # VRP component (0-20 points) — VRP typically -5 to +15
    if vrp_val is not None:
        vrp_score = min(20, max(0, (vrp_val + 5) / 20 * 20))
    else:
        vrp_score = 10
    components["VRP"] = vrp_score

    total = sum(components.values())

    if total < 25:
        zone = "Complacent"
        color = "#00ff88"
    elif total < 45:
        zone = "Low Fear"
        color = "#88cc00"
    elif total < 60:
        zone = "Moderate"
        color = "#ffaa00"
    elif total < 80:
        zone = "Elevated"
        color = "#ff6600"
    else:
        zone = "Extreme Fear"
        color = "#ff4444"

    return {
        "value": round(total, 1),
        "zone": zone,
        "color": color,
        "components": components,
    }

def compute_max_pain(records, spot):
    """Calculate max pain strike — the strike where option writers have minimum payout."""
    if not isinstance(records, (list, tuple)) or len(records) == 0: return None, {}
    strikes = sorted(set(r.get("strike", 0) for r in records if r.get("strike", 0) > 0))
    if not strikes: return None, {}

    pain = {}
    for strike in strikes:
        total_pain = 0
        for r in records:
            k = r.get("strike", 0)
            if k <= 0: continue
            oi_c = _safe_int(r.get("oi_call"))
            oi_p = _safe_int(r.get("oi_put"))
            # Call pain: max(0, strike - k) * OI
            total_pain += max(0, strike - k) * oi_c
            # Put pain: max(0, k - strike) * OI
            total_pain += max(0, k - strike) * oi_p
        pain[strike] = total_pain

    if not pain: return None, {}
    max_pain_strike = min(pain, key=pain.get)
    return max_pain_strike, pain

def compute_gamma_walls(records, spot, top_n=5):
    """Identify strikes with highest absolute GEX (gamma walls)."""
    if not isinstance(records, (list, tuple)) or len(records) == 0: return []
    walls = []
    for r in records:
        strike = r.get("strike", 0)
        if strike <= 0: continue
        gex_c = _safe_float(r.get("gamma_call")) * _safe_int(r.get("oi_call")) * spot**2 / 100
        gex_p = -_safe_float(r.get("gamma_put")) * _safe_int(r.get("oi_put")) * spot**2 / 100
        net_gex = gex_c + gex_p
        walls.append({"strike": strike, "gex": net_gex, "abs_gex": abs(net_gex)})
    walls.sort(key=lambda x: x["abs_gex"], reverse=True)
    return walls[:top_n]

def compute_delta_neutral_strike(records, spot):
    """Find the strike closest to delta-neutral (call delta + put delta ~ 0)."""
    if not isinstance(records, (list, tuple)) or len(records) == 0: return None
    best_strike = None
    best_diff = float('inf')
    for r in records:
        strike = r.get("strike", 0)
        if strike <= 0: continue
        dc = _safe_float(r.get("delta_call"))
        dp = _safe_float(r.get("delta_put"))
        diff = abs(dc + dp)
        if diff < best_diff:
            best_diff = diff
            best_strike = strike
    return best_strike

def compute_term_structure_metrics(vix_terms_dict):
    """Compute contango/backwardation, slope, and curvature of VIX term structure."""
    if not vix_terms_dict or len(vix_terms_dict) < 2:
        return {"regime": "Unknown", "spread": 0, "slope": 0, "curvature": 0, "roll_yield": 0}

    names = list(vix_terms_dict.keys())
    vals = list(vix_terms_dict.values())

    spread = vals[1] - vals[0] if len(vals) >= 2 else 0
    slope = spread / max(len(names)-1, 1)

    # Curvature (second derivative approximation)
    if len(vals) >= 3:
        curvature = vals[2] - 2*vals[1] + vals[0]
    else:
        curvature = 0

    # Roll yield: annualized return from rolling front-month to spot
    if vals[0] > 0:
        roll_yield = (vals[0] - vals[1]) / vals[0] * 12 * 100  # Annualized pct
    else:
        roll_yield = 0

    if spread > 3: regime = "Strong Contango"
    elif spread > 0.5: regime = "Contango"
    elif spread > -0.5: regime = "Flat"
    elif spread > -3: regime = "Backwardation"
    else: regime = "Strong Backwardation"

    return {
        "regime": regime,
        "spread": round(spread, 2),
        "slope": round(slope, 3),
        "curvature": round(curvature, 3),
        "roll_yield": round(roll_yield, 2),
    }

def compute_cross_asset_vol(vix_data_dict):
    """Compute cross-asset vol correlation and dispersion."""
    # Build DataFrame of vol indices
    dfs = {}
    for name, series in vix_data_dict.items():
        if series is not None and len(series) > 20:
            s = series.dropna()
            if len(s) > 20:
                dfs[name] = s

    if len(dfs) < 2:
        return {"correlation": pd.DataFrame(), "dispersion": {}, "current": {}}

    df = pd.DataFrame(dfs)
    # Compute returns
    returns = df.pct_change().dropna()
    corr = returns.corr() if len(returns) > 5 else pd.DataFrame()

    # Current dispersion (cross-sectional vol of vols)
    current_vals = {name: float(series.iloc[-1]) for name, series in dfs.items()}
    if len(current_vals) >= 2:
        vol_of_vols = float(np.std(list(current_vals.values())) / np.mean(list(current_vals.values())) * 100)
    else:
        vol_of_vols = 0.0

    return {
        "correlation": corr,
        "dispersion": {"vol_of_vols": vol_of_vols},
        "current": current_vals,
    }

# =============================================================================
# INSTITUTIONAL MATPLOTLIB STYLE
# =============================================================================
plt.rcParams.update({
    'figure.facecolor': '#0a0e1a',
    'axes.facecolor': '#1a2332',
    'axes.edgecolor': '#2a3a5a',
    'axes.labelcolor': '#8899aa',
    'text.color': '#c8d6e5',
    'xtick.color': '#8899aa',
    'ytick.color': '#8899aa',
    'grid.color': '#1e2d4a',
    'grid.alpha': 0.4,
    'legend.facecolor': '#111a2a',
    'legend.edgecolor': '#2a3a5a',
    'legend.labelcolor': '#c8d6e5',
    'font.family': 'monospace',
    'font.size': 9,
    'axes.titlesize': 11,
    'axes.titleweight': 'bold',
})

st.set_page_config(page_title="MK Quant Monitor", layout="wide", initial_sidebar_state="expanded")

st.markdown("""<style>
:root{--bg-main:#0a0e1a;--bg-panel:#111a2a;--bg-card:#1a2332;--border:#2a3a5a;--text-primary:#c8d6e5;--text-secondary:#8899aa;--cyan:#00d4ff;--green:#00ff88;--red:#ff4444;--yellow:#ffaa00;--orange:#ff6600}
body{background-color:var(--bg-main)!important;color:var(--text-primary)!important}
.reportview-container .main .block-container{padding-top:0.5rem;max-width:1600px}
.stButton>button{background-color:var(--bg-card);color:var(--text-primary);border:1px solid var(--border);border-radius:4px}
.stButton>button:hover{background-color:var(--bg-panel);border-color:var(--cyan)}
div[data-testid="stMetric"]{background:var(--bg-card);border:1px solid var(--border);border-radius:4px;padding:10px 14px}
div[data-testid="stMetric"] label{color:var(--text-secondary)!important;font-size:0.7rem!important;text-transform:uppercase;letter-spacing:0.08em;font-family:sans-serif!important}
div[data-testid="stMetric"] div[data-testid="stMetricValue"]{color:var(--text-primary)!important;font-size:1.3rem!important;font-weight:600}
div[data-testid="stMetric"] div[data-testid="stMetricDelta"]{font-size:0.8rem!important}
.stTabs [data-baseweb="tab-list"]{gap:2px;border-bottom:1px solid var(--border)}
.stTabs [data-baseweb="tab"]{background:var(--bg-card);border:1px solid var(--border);border-bottom:none;border-radius:4px 4px 0 0;color:var(--text-secondary);padding:8px 20px;font-weight:500;font-family:sans-serif;font-size:0.85rem;letter-spacing:0.02em}
.stTabs [aria-selected="true"]{background:var(--bg-panel)!important;color:var(--cyan)!important;border-color:var(--cyan)!important;border-bottom:none!important}
footer{visibility:hidden}
.wm{position:fixed;bottom:6px;right:10px;font-size:0.6rem;font-style:italic;color:#3a4a5a;opacity:0.7;z-index:999}
section[data-testid="stSidebar"]{background:var(--bg-panel)!important}
div[data-testid="stDataFrame"]{border:1px solid var(--border)!important}
.stExpander{border:1px solid var(--border)!important;border-radius:4px!important}
</style><div class="wm">Krupp Capital</div>""", unsafe_allow_html=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mk_quant")
DEMO_MODE = os.environ.get("DEMO_MODE","0")=="1"
HEADERS = {"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36","Accept":"application/json"}

# =============================================================================
# DATA FETCHERS
# =============================================================================

@st.cache_data(ttl=60)
def fetch_yahoo():
    import requests
    out = {}
    symbols = {
        "SPX":"^SPX","NDX":"^NDX","DJX":"^DJX","RUT":"^RUT",
        "GC1!":"GC=F","CL1!":"CL=F","FDAX1!":"FDAX.F","ES1!":"ES=F","NQ1!":"NQ=F",
        "ESTX50":"^STOXX50E","NIKKEI":"^N225","DAX":"^GDAXI",
        "VIX":"^VIX","VVIX":"^VVIX","VXD":"^VXD","VXN":"^VXN","RVX":"^RVX",
        "OVX":"^OVX","GVZ":"^GVZ","VXEEM":"^VXEEM",
        "VSTOXX":"V2TX.DE",
        "VDAX":"V1X.DE",
        "BVIX":"BVIX",
        "EVIV":"EVIV",
        "BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
        "SPY":"SPY","QQQ":"QQQ","TLT":"TLT","HYG":"HYG","GLD":"GLD","USO":"USO",
    }
    for name, sym in symbols.items():
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range":"6mo","interval":"1d"}, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                result = r.json().get("chart",{}).get("result",[{}])[0]
                ts, closes = result.get("timestamp",[]), result.get("indicators",{}).get("quote",[{}])[0].get("close",[])
                if ts and closes:
                    out[name] = pd.DataFrame({"Close":closes}, index=pd.to_datetime(ts,unit="s")).dropna()
        except Exception as e:
            logger.debug(f"Yahoo {sym}: {e}")
    try:
        import yfinance as yf
        for name, sym in symbols.items():
            if name not in out:
                try:
                    hist = yf.Ticker(sym).history(period="6mo")
                    if hist is not None and not hist.empty and "Close" in hist.columns:
                        out[name] = hist[["Close"]].dropna()
                except: pass
    except ImportError: pass
    return out


@st.cache_data(ttl=120)
def fetch_cboe_spx_chain():
    result = cboe_fetch_spx_chain()
    if result is not None:
        spot = result["spot"]
        records = result["records"]
        expirations = result["expirations"]
        normalized = []
        for r in records:
            normalized.append({
                "strike": r["strike"], "expiry": r["expiry"],
                "oi_call": r["open_interest"] if r["option_type"] == "C" else 0,
                "oi_put": r["open_interest"] if r["option_type"] == "P" else 0,
                "iv_call": r["iv"] if r["option_type"] == "C" else 0,
                "iv_put": r["iv"] if r["option_type"] == "P" else 0,
                "delta_call": r["delta"] if r["option_type"] == "C" else 0,
                "delta_put": r["delta"] if r["option_type"] == "P" else 0,
                "gamma_call": r["gamma"] if r["option_type"] == "C" else 0,
                "gamma_put": r["gamma"] if r["option_type"] == "P" else 0,
                "theta_call": r["theta"] if r["option_type"] == "C" else 0,
                "theta_put": r["theta"] if r["option_type"] == "P" else 0,
                "vega_call": r["vega"] if r["option_type"] == "C" else 0,
                "vega_put": r["vega"] if r["option_type"] == "P" else 0,
                "vanna_call": r.get("vanna", 0) if r["option_type"] == "C" else 0,
                "vanna_put": r.get("vanna", 0) if r["option_type"] == "P" else 0,
                "bid_call": r["bid"] if r["option_type"] == "C" else 0,
                "bid_put": r["bid"] if r["option_type"] == "P" else 0,
                "ask_call": r["ask"] if r["option_type"] == "C" else 0,
                "ask_put": r["ask"] if r["option_type"] == "P" else 0,
            })
        merged = {}
        for r in normalized:
            k = r["strike"]
            if k not in merged:
                merged[k] = r.copy()
            else:
                for key in ["oi_call","oi_put","iv_call","iv_put","delta_call","delta_put",
                           "gamma_call","gamma_put","theta_call","theta_put",
                           "vega_call","vega_put","vanna_call","vanna_put",
                           "bid_call","bid_put","ask_call","ask_put"]:
                    if r[key] != 0:
                        merged[k][key] = r[key]
        return {"spot": spot, "records": list(merged.values()), "expirations": expirations,
                "source": "cboe_live", "timestamp": datetime.utcnow().isoformat()}
    return None


@st.cache_data(ttl=60)
def fetch_deribit():
    import requests
    base = "https://www.deribit.com/api/v2/public"
    result = {"perpetuals":{}, "options":{}, "orderbooks":{}, "trades":{}}
    for inst in ["BTC-PERPETUAL","ETH-PERPETUAL"]:
        try:
            d = requests.get(f"{base}/ticker?instrument_name={inst}", timeout=10).json().get("result",{})
            if d:
                sym = inst.split("-")[0].lower()
                result["perpetuals"][sym] = {"last":d.get("last_price"),"mark":d.get("mark_price"),"index":d.get("index_price"),"high":d["stats"].get("high"),"low":d["stats"].get("low"),"change_pct":d["stats"].get("price_change"),"volume":d["stats"].get("volume"),"volume_usd":d["stats"].get("volume_usd"),"oi":d.get("open_interest"),"funding_8h":d.get("funding_8h"),"best_bid":d.get("best_bid_price"),"best_ask":d.get("best_ask_price"),"best_bid_amt":d.get("best_bid_amount"),"best_ask_amt":d.get("best_ask_amount")}
        except Exception as e:
            logger.debug(f"Deribit {inst}: {e}")
    for cur in ["BTC","ETH"]:
        try:
            insts = requests.get(f"{base}/get_instruments?currency={cur}&kind=option&expired=false", timeout=15).json().get("result",[])
            result["options"][cur.lower()] = insts
        except Exception as e:
            logger.debug(f"Deribit options {cur}: {e}")
    for inst in ["BTC-PERPETUAL","ETH-PERPETUAL"]:
        try:
            trades = requests.get(f"{base}/get_last_trades_by_instrument?instrument_name={inst}&count=20", timeout=10).json().get("result",{}).get("trades",[])
            result["trades"][inst.split("-")[0].lower()] = trades
        except Exception as e:
            logger.debug(f"Deribit trades {inst}: {e}")
    for inst in ["BTC-PERPETUAL","ETH-PERPETUAL"]:
        try:
            ob = requests.get(f"{base}/get_order_book?instrument_name={inst}&depth=10", timeout=10).json().get("result",{})
            result["orderbooks"][inst.split("-")[0].lower()] = {"bids":ob.get("bids",[])[:10],"asks":ob.get("asks",[])[:10],"mark_price":ob.get("mark_price"),"index_price":ob.get("index_price")}
        except Exception as e:
            logger.debug(f"Deribit ob {inst}: {e}")
    return result


@st.cache_data(ttl=60)
def fetch_coingecko():
    import requests
    try:
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency":"usd","order":"market_cap_desc","per_page":50,"page":"1","sparkline":"true","price_change_percentage":"1h,24h,7d"}, timeout=10)
        if r.status_code == 200:
            out = {}
            for c in r.json():
                sym = c["symbol"].upper()
                out[sym] = {"name":c["name"],"price":c["current_price"],"mkt_cap":c["market_cap"],"vol_24h":c["total_volume"],"chg_1h":c.get("price_change_percentage_1h_in_currency"),"chg_24h":c.get("price_change_percentage_24h"),"chg_7d":c.get("price_change_percentage_7d_in_currency"),"high_24h":c.get("high_24h"),"low_24h":c.get("low_24h"),"sparkline":c.get("sparkline_in_7d",{}).get("price",[]),"rank":c.get("market_cap_rank"),"ath":c.get("ath"),"ath_change":c.get("ath_change_percentage")}
            return out
    except Exception as e:
        logger.debug(f"CoinGecko: {e}")
    return {}


@st.cache_data(ttl=60)
def fetch_fear_greed():
    import requests
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=7", timeout=10)
        data = r.json()
        if "data" in data and len(data["data"]) > 0:
            latest = data["data"][0]
            return {"value":int(latest.get("value",0)),"label":latest.get("value_classification","Unknown"),"history":[{"date":datetime.fromtimestamp(int(d["timestamp"])).strftime("%m-%d"),"value":int(d["value"])} for d in data["data"]]}
    except Exception as e:
        logger.debug(f"FearGreed: {e}")
    return None


@st.cache_data(ttl=300)
def fetch_congress():
    import requests
    BASE = "https://congressinfor-production.up.railway.app"
    result = {"trades":[],"health":{}}
    try:
        h = requests.get(f"{BASE}/health", timeout=10).json()
        result["health"] = h
    except: pass
    try:
        r = requests.get(f"{BASE}/trades/recent", params={"limit":100,"days":30}, timeout=10)
        data = r.json()
        result["trades"] = data.get("trades", [])
    except Exception as e:
        logger.debug(f"Congress: {e}")
    return result


@st.cache_data(ttl=300)
def fetch_fred(series_id, limit=100):
    import requests
    key = os.environ.get("FRED_API_KEY","")
    if not key: return None
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
            params={"series_id":series_id,"api_key":key,"file_type":"json","sort_order":"desc","limit":limit}, timeout=10)
        if r.status_code == 200:
            obs = r.json().get("observations",[])
            if obs:
                df = pd.DataFrame(obs)
                df["date"] = pd.to_datetime(df["date"])
                df["value"] = pd.to_numeric(df["value"],errors="coerce")
                return df.dropna(subset=["value"]).set_index("date")["value"]
    except: pass
    return None


# =============================================================================
# HELPERS
# =============================================================================
def _s(df):
    if df is None: return None
    if isinstance(df,tuple):
        for x in df:
            if hasattr(x,"columns"): df=x; break
    if hasattr(df,"columns"):
        try:
            s = df["Close"] if "Close" in df.columns else df.iloc[:,0]
            return pd.to_numeric(s,errors="coerce").dropna()
        except: return None
    return None

def fmt_pct(v):
    if v is None: return "N/A"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"

def fmt_price(v):
    if v is None: return "N/A"
    if abs(v) >= 1000: return f"${v:,.2f}"
    if abs(v) >= 1: return f"${v:,.2f}"
    return f"${v:.4f}"

def sign_prefix(v):
    if v is None: return ""
    return "+" if v >= 0 else ""

# =============================================================================
# ASSET -> VOLA MAPPING
# =============================================================================
ASSET_VOLA_MAP = {
    "SPX":   {"vola":"VIX",    "name":"S&P 500"},
    "NDX":   {"vola":"VXN",    "name":"Nasdaq 100"},
    "DJX":   {"vola":"VXD",    "name":"Dow Jones"},
    "RUT":   {"vola":"RVX",    "name":"Russell 2000"},
    "GC1!":  {"vola":"GVZ",    "name":"Gold"},
    "CL1!":  {"vola":"OVX",    "name":"Crude Oil"},
    "FDAX1!":{"vola":"V1X.DE", "name":"DAX"},
    "ESTX50":{"vola":"V2TX.DE","name":"Euro Stoxx 50"},
    "NIKKEI":{"vola":"VHSI",   "name":"Nikkei 225"},
    "BTC":   {"vola":"BVIX",   "name":"Bitcoin"},
    "ETH":   {"vola":"EVIV",   "name":"Ethereum"},
}

# =============================================================================
# MATPLOTLIB CHARTS
# =============================================================================

def chart_gex_profile(crash_profile, spot, zero_gamma):
    fig, ax = plt.subplots(figsize=(12, 5))
    if crash_profile is None:
        crash_profile = []
    df = pd.DataFrame(crash_profile)
    if df.empty or "spot_pct" not in df.columns or "gex_plus" not in df.columns:
        return fig
    ax.fill_between(df["spot_pct"], df["gex_plus"], 0, where=(df["gex_plus"]>=0).values, alpha=0.25, color="#00ff88", label="Long Gamma")
    ax.fill_between(df["spot_pct"], df["gex_plus"], 0, where=(df["gex_plus"]<0).values, alpha=0.25, color="#ff4444", label="Short Gamma")
    ax.plot(df["spot_pct"], df["gex_plus"], color="#00d4ff", linewidth=1.8)
    ax.axhline(0, color="#2a3a5a", linewidth=1, linestyle="-")
    ax.axvline(0, color="#8899aa", linewidth=0.5, linestyle=":", alpha=0.4)
    zg_pct = (zero_gamma/spot-1)*100
    ax.axvline(zg_pct, color="#ff6600", linewidth=1.5, linestyle="--", label=f"Zero Gamma: {zero_gamma:,.0f}")
    ax.set_xlabel("Spot Move (%)", fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("GEX+ ($)", fontsize=9, fontfamily="sans-serif")
    ax.set_title("GEX+ Crash Profile -- Gamma Exposure vs Spot Move", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:+.0f}%"))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"${x/1e9:.1f}B" if abs(x)>=1e9 else f"${x/1e6:.0f}M"))
    plt.tight_layout()
    return fig

def chart_oi_by_strike(records, spot):
    fig, ax = plt.subplots(figsize=(14, 5))
    if records is None:
        records = []
    df = pd.DataFrame(records)
    if df.empty or "strike" not in df.columns:
        return fig
    df = df.sort_values("strike")
    strikes = df["strike"].values
    width = (strikes[1]-strikes[0])*0.35 if len(strikes)>1 else 5
    oi_call = df.get("oi_call", pd.Series(0, index=df.index)).values
    oi_put = df.get("oi_put", pd.Series(0, index=df.index)).values
    ax.bar(strikes - width/2, oi_call, width=width, color="#00ff88", alpha=0.7, label="Call OI")
    ax.bar(strikes + width/2, -oi_put, width=width, color="#ff4444", alpha=0.7, label="Put OI")
    ax.axvline(spot, color="#ffaa00", linewidth=1.5, linestyle="--", label=f"Spot: {spot:,.0f}")
    ax.set_xlabel("Strike", fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("Open Interest", fontsize=9, fontfamily="sans-serif")
    ax.set_title("Open Interest Profile by Strike", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
    ax.grid(True, alpha=0.3, axis="y")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:,.0f}"))
    plt.tight_layout()
    return fig

def chart_gex_by_strike(records, spot):
    fig, ax = plt.subplots(figsize=(14, 5))
    if records is None:
        records = []
    df = pd.DataFrame(records)
    if df.empty or "strike" not in df.columns:
        return fig
    df = df.sort_values("strike")
    strikes = df["strike"].values
    g_c = df.get("gamma_call", pd.Series(0, index=df.index)).values
    g_p = df.get("gamma_put", pd.Series(0, index=df.index)).values
    o_c = df.get("oi_call", pd.Series(0, index=df.index)).values
    o_p = df.get("oi_put", pd.Series(0, index=df.index)).values
    gex_call = g_c * o_c * spot**2 / 100
    gex_put = -g_p * o_p * spot**2 / 100
    net_gex = gex_call + gex_put
    width = (strikes[1]-strikes[0])*0.7 if len(strikes)>1 else 5
    colors = ["#00ff88" if v >= 0 else "#ff4444" for v in net_gex]
    ax.bar(strikes, net_gex/1e9, width=width, color=colors, alpha=0.75)
    ax.axvline(spot, color="#ffaa00", linewidth=1.5, linestyle="--", label=f"Spot: {spot:,.0f}")
    ax.axhline(0, color="#2a3a5a", linewidth=1)
    ax.set_xlabel("Strike", fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("Net GEX ($B)", fontsize=9, fontfamily="sans-serif")
    ax.set_title("Gamma Exposure (GEX) by Strike", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
    ax.grid(True, alpha=0.3, axis="y")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:,.0f}"))
    plt.tight_layout()
    return fig

def chart_iv_skew(records, spot):
    fig, ax = plt.subplots(figsize=(12, 5))
    if records is None:
        records = []
    df = pd.DataFrame(records)
    if df.empty or "strike" not in df.columns:
        return fig
    df = df.sort_values("strike")
    moneyness = (df["strike"] / spot - 1) * 100
    iv_c = df.get("iv_call", pd.Series(0, index=df.index)).values
    iv_p = df.get("iv_put", pd.Series(0, index=df.index)).values
    ax.plot(moneyness, iv_c*100, color="#00d4ff", linewidth=1.8, label="Call IV", marker="o", markersize=2.5)
    ax.plot(moneyness, iv_p*100, color="#ff6600", linewidth=1.8, label="Put IV", marker="o", markersize=2.5)
    ax.fill_between(moneyness, iv_c*100, iv_p*100, alpha=0.08, color="#00d4ff")
    ax.axvline(0, color="#ffaa00", linewidth=1.2, linestyle="--", label="ATM")
    ax.set_xlabel("Moneyness (%)", fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("Implied Volatility (%)", fontsize=9, fontfamily="sans-serif")
    ax.set_title("Implied Volatility Skew", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:+.0f}%"))
    plt.tight_layout()
    return fig

def chart_vol_smile(records, spot, expiry=None):
    fig, ax = plt.subplots(figsize=(12, 5))
    if records is None:
        records = []
    df = pd.DataFrame(records)
    if df.empty or "strike" not in df.columns:
        return fig
    df = df.sort_values("strike")
    if expiry:
        df = df[df.get("expiry","") == expiry] if "expiry" in df.columns else df
    iv_c = df.get("iv_call", pd.Series(0, index=df.index)).values
    iv_p = df.get("iv_put", pd.Series(0, index=df.index)).values
    ax.plot(df["strike"], iv_c*100, color="#00d4ff", linewidth=1.8, label="Call IV", marker="o", markersize=3)
    ax.plot(df["strike"], iv_p*100, color="#ff6600", linewidth=1.8, label="Put IV", marker="s", markersize=3)
    ax.axvline(spot, color="#ffaa00", linewidth=1.2, linestyle="--", label=f"Spot: {spot:,.0f}")
    ax.set_xlabel("Strike", fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("Implied Volatility (%)", fontsize=9, fontfamily="sans-serif")
    title = "Volatility Smile" + (f" -- Exp: {expiry}" if expiry else "")
    ax.set_title(title, fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:,.0f}"))
    plt.tight_layout()
    return fig

def chart_heatmap(heatmap_data):
    fig, ax = plt.subplots(figsize=(10, 6))
    values = np.array(heatmap_data["values"])
    spot_range = heatmap_data["spot_range"]
    iv_range = heatmap_data["iv_range"]
    im = ax.imshow(values, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-1, vmax=1,
                   extent=[iv_range[0], iv_range[1], spot_range[0], spot_range[1]])
    ax.set_xlabel("IV Shock (vol pts)", fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("Spot Move (%)", fontsize=9, fontfamily="sans-serif")
    ax.set_title("GEX+ Heatmap -- Spot Move vs IV Shock (Normalized)", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Normalized GEX+", fontsize=8, fontfamily="sans-serif")
    cbar.ax.yaxis.set_tick_params(color='#8899aa')
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color='#8899aa')
    ax.grid(True, alpha=0.15)
    plt.tight_layout()
    return fig

def chart_term_structure(vix_terms_dict):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    names = list(vix_terms_dict.keys())
    vals = list(vix_terms_dict.values())
    x_pos = range(len(names))
    if len(vals) >= 2:
        spread = vals[1] - vals[0]
        is_contango = spread > 0
    else:
        is_contango = True
        spread = 0
    bar_colors = []
    for i, v in enumerate(vals):
        if i == 0:
            bar_colors.append("#00d4ff")
        else:
            bar_colors.append("#00ff88" if (is_contango and v >= vals[0]) or (not is_contango and v >= vals[0]) else "#ff4444")
    ax.bar(x_pos, vals, color=bar_colors, alpha=0.6, width=0.5, zorder=2)
    ax.plot(x_pos, vals, color="#00d4ff", linewidth=2, marker="o", markersize=6, zorder=3)
    if len(vals) >= 2:
        regime_label = "CONTANGO" if is_contango else "BACKWARDATION"
        regime_color = "#00ff88" if is_contango else "#ff4444"
        ax.annotate(f"Spread: {spread:+.2f}  [{regime_label}]",
                   xy=(0.02, 0.95), xycoords="axes fraction",
                   fontsize=10, color=regime_color, ha="left", va="top",
                   fontweight="bold", fontfamily="sans-serif")
    ax.set_xticks(list(x_pos))
    ax.set_xticklabels(names, fontsize=9, fontfamily="sans-serif")
    ax.set_ylabel("VIX Level", fontsize=9, fontfamily="sans-serif")
    ax.set_title("VIX Term Structure", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    ax.grid(True, alpha=0.3, axis="y")
    ax.set_axisbelow(True)
    plt.tight_layout()
    return fig

def chart_fear_greed_gauge(fg_value, fg_label):
    fig, ax = plt.subplots(figsize=(6, 3.5), subplot_kw=dict(projection='polar'))
    theta_start = np.pi * 0.15
    theta_end = np.pi * 0.85
    theta_range = theta_end - theta_start
    segments = [(0,25,"#ff4444","Extreme Fear"),(25,45,"#ff6600","Fear"),(45,55,"#ffaa00","Neutral"),(55,75,"#88cc00","Greed"),(75,100,"#00ff88","Extreme Greed")]
    for lo, hi, color, _ in segments:
        t_lo = theta_start + theta_range * lo / 100
        t_hi = theta_start + theta_range * hi / 100
        t_seg = np.linspace(t_lo, t_hi, 30)
        ax.fill_between(t_seg, 0.6, 1.0, color=color, alpha=0.25)
    needle_theta = theta_start + theta_range * fg_value / 100
    ax.annotate('', xy=(needle_theta, 0.85), xytext=(needle_theta, 0),
                arrowprops=dict(arrowstyle='->', color='#c8d6e5', lw=2.5))
    ax.plot(needle_theta, 0, 'o', color='#00d4ff', markersize=6, zorder=5)
    ax.text(np.pi/2, 0.35, str(fg_value), ha='center', va='center', fontsize=28, fontweight='bold', color='#c8d6e5', fontfamily='monospace', transform=ax.transAxes)
    ax.text(np.pi/2, 0.15, fg_label.upper(), ha='center', va='center', fontsize=9, color='#8899aa', fontfamily='sans-serif', transform=ax.transAxes)
    ax.set_ylim(0, 1.1)
    ax.set_xlim(theta_start - 0.05, theta_end + 0.05)
    ax.set_axis_off()
    ax.set_title("Fear & Greed Index", fontsize=11, fontweight="bold", pad=15, fontfamily="sans-serif", y=1.05)
    plt.tight_layout()
    return fig

def chart_vol_heatmap(vol_data):
    fig, ax = plt.subplots(figsize=(12, 3))
    names = list(vol_data.keys())
    values = [vol_data[n]["value"] for n in names]
    changes = [vol_data[n].get("change") for n in names]
    ivr_values = [vol_data[n].get("ivr") for n in names]
    n = len(names)
    cell_width = 1.0
    cell_height = 1.0
    for i, (name, val, chg, ivr) in enumerate(zip(names, values, changes, ivr_values)):
        if val < 15:
            bg_color = "#0a2a1a"; text_color = "#00ff88"
        elif val < 25:
            bg_color = "#2a2a0a"; text_color = "#ffaa00"
        elif val < 35:
            bg_color = "#2a1a0a"; text_color = "#ff6600"
        else:
            bg_color = "#2a0a0a"; text_color = "#ff4444"
        rect = Rectangle((i * cell_width, 0), cell_width * 0.95, cell_height * 0.95, facecolor=bg_color, edgecolor="#2a3a5a", linewidth=1)
        ax.add_patch(rect)
        ax.text(i * cell_width + cell_width/2, cell_height * 0.75, name, ha='center', va='center', fontsize=10, fontweight='bold', color='#c8d6e5', fontfamily='sans-serif')
        ax.text(i * cell_width + cell_width/2, cell_height * 0.50, f"{val:.2f}", ha='center', va='center', fontsize=14, fontweight='bold', color=text_color, fontfamily='monospace')
        chg_str = f"{sign_prefix(chg)}{chg:.2f}%" if chg is not None else "N/A"
        chg_color = "#00ff88" if chg is not None and chg >= 0 else "#ff4444" if chg is not None else "#8899aa"
        ax.text(i * cell_width + cell_width/2, cell_height * 0.30, chg_str, ha='center', va='center', fontsize=8, color=chg_color, fontfamily='monospace')
        ivr_str = f"IVR: {ivr:.0f}%" if ivr is not None else ""
        ax.text(i * cell_width + cell_width/2, cell_height * 0.12, ivr_str, ha='center', va='center', fontsize=7, color='#8899aa', fontfamily='sans-serif')
    ax.set_xlim(0, n * cell_width)
    ax.set_ylim(0, cell_height)
    ax.set_aspect('equal')
    ax.set_axis_off()
    ax.set_title("Volatility Index Monitor", fontsize=11, fontweight="bold", pad=12, fontfamily="sans-serif")
    plt.tight_layout()
    return fig

def chart_order_book(orderbook, symbol):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    if bids:
        bid_prices = [b[0] for b in bids]
        bid_amounts = [b[1] for b in bids]
        ax1.barh(range(len(bids)), bid_amounts, color="#00ff88", alpha=0.7, height=0.8)
        ax1.set_yticks(range(len(bids)))
        ax1.set_yticklabels([f"{p:,.0f}" for p in bid_prices], fontsize=7)
        ax1.set_xlabel("Size", fontsize=8, fontfamily="sans-serif")
        ax1.set_title(f"{symbol.upper()} -- Bids", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax1.invert_yaxis()
        ax1.grid(True, alpha=0.2, axis="x")
    if asks:
        ask_prices = [a[0] for a in asks]
        ask_amounts = [a[1] for a in asks]
        ax2.barh(range(len(asks)), ask_amounts, color="#ff4444", alpha=0.7, height=0.8)
        ax2.set_yticks(range(len(asks)))
        ax2.set_yticklabels([f"{p:,.0f}" for p in ask_prices], fontsize=7)
        ax2.set_xlabel("Size", fontsize=8, fontfamily="sans-serif")
        ax2.set_title(f"{symbol.upper()} -- Asks", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax2.grid(True, alpha=0.2, axis="x")
    plt.tight_layout()
    return fig

def chart_sparkline(prices, symbol, current_price):
    fig, ax = plt.subplots(figsize=(3, 1.2))
    if prices:
        ax.plot(prices, color="#00d4ff", linewidth=1.2)
        ax.fill_between(range(len(prices)), prices, min(prices), alpha=0.15, color="#00d4ff")
    ax.set_axis_off()
    ax.set_title(f"{symbol}  {fmt_price(current_price)}", fontsize=7, fontfamily="sans-serif", color="#c8d6e5", pad=4, loc="left")
    plt.tight_layout(pad=0.2)
    return fig

def chart_bl_forecast(fc_1d, fc_1w):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    for ax, fc, label in [(ax1, fc_1d, "1-Day Forecast"), (ax2, fc_1w, "1-Week Forecast")]:
        p5, p25, median, p75, p95 = fc["p5"], fc["p25"], fc["median"], fc["p75"], fc["p95"]
        ax.plot([p5, p5], [0.3, 0.7], color="#8899aa", linewidth=1)
        ax.plot([p95, p95], [0.3, 0.7], color="#8899aa", linewidth=1)
        ax.plot([p5, p95], [0.5, 0.5], color="#8899aa", linewidth=1, linestyle="--")
        rect = Rectangle((p25, 0.3), p75-p25, 0.4, facecolor="#00d4ff", alpha=0.2, edgecolor="#00d4ff", linewidth=1.5)
        ax.add_patch(rect)
        ax.plot([median, median], [0.3, 0.7], color="#ffaa00", linewidth=2)
        ax.text(p5, 0.15, f"{p5:,.0f}", ha='center', fontsize=7, color="#8899aa", fontfamily="monospace")
        ax.text(p95, 0.15, f"{p95:,.0f}", ha='center', fontsize=7, color="#8899aa", fontfamily="monospace")
        ax.text(median, 0.85, f"{median:,.0f}", ha='center', fontsize=8, color="#ffaa00", fontweight="bold", fontfamily="monospace")
        ax.text(p25, 0.85, f"{p25:,.0f}", ha='center', fontsize=7, color="#8899aa", fontfamily="monospace")
        ax.text(p75, 0.85, f"{p75:,.0f}", ha='center', fontsize=7, color="#8899aa", fontfamily="monospace")
        ax.set_xlim(p5 * 0.998, p95 * 1.002)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.set_xlabel("Price Level", fontsize=8, fontfamily="sans-serif")
        ax.set_title(f"{label}  (sigma: {fc['sigma_pct']:.1f}%)", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:,.0f}"))
        ax.grid(True, alpha=0.2, axis="x")
    plt.tight_layout()
    return fig

def chart_pc_ratios(records):
    """Put/Call OI and Volume Ratios by Expiry."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 4))
    if records is None:
        records = []
    df = pd.DataFrame(records)
    if not df.empty and "expiry" in df.columns and "oi_call" in df.columns and "oi_put" in df.columns:
        by_exp = df.groupby("expiry").agg({"oi_call":"sum","oi_put":"sum"}).reset_index()
        by_exp["pc_oi"] = by_exp["oi_put"] / by_exp["oi_call"].replace(0,1)
        by_exp = by_exp.sort_values("expiry")
        colors = ["#ff4444" if v > 1 else "#00ff88" for v in by_exp["pc_oi"]]
        ax1.bar(range(len(by_exp)), by_exp["pc_oi"], color=colors, alpha=0.8)
        ax1.set_xticks(range(len(by_exp)))
        ax1.set_xticklabels(by_exp["expiry"], rotation=45, ha="right", fontsize=8)
        ax1.axhline(1, color="#ffaa00", linewidth=1, linestyle="--", label="P/C = 1")
        ax1.set_ylabel("Put/Call OI Ratio", fontsize=9, fontfamily="sans-serif")
        ax1.set_title("Put/Call OI Ratio by Expiry", fontsize=11, fontweight="bold", fontfamily="sans-serif")
        ax1.legend(fontsize=8)
        ax1.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    return fig


# =============================================================================
# VOLATILITY VINTAGE CHART FUNCTIONS
# =============================================================================

def chart_vvix_gauge(vvix_val, vvix_pct):
    """VVIX gauge showing current level and percentile."""
    fig = plt.figure(figsize=(10, 3.5))

    # Left: polar gauge
    ax1 = fig.add_subplot(121, projection='polar')
    theta_start = np.pi * 0.15
    theta_end = np.pi * 0.85
    theta_range = theta_end - theta_start
    segments = [(0,25,"#00ff88","Low Vol"),(25,50,"#88cc00","Normal"),(50,75,"#ffaa00","Elevated"),(75,100,"#ff4444","Extreme")]
    for lo, hi, color, _ in segments:
        t_lo = theta_start + theta_range * lo / 100
        t_hi = theta_start + theta_range * hi / 100
        t_seg = np.linspace(t_lo, t_hi, 30)
        ax1.fill_between(t_seg, 0.6, 1.0, color=color, alpha=0.25)
    needle_theta = theta_start + theta_range * vvix_pct / 100
    ax1.annotate('', xy=(needle_theta, 0.85), xytext=(needle_theta, 0),
                arrowprops=dict(arrowstyle='->', color='#c8d6e5', lw=2.5))
    ax1.plot(needle_theta, 0, 'o', color='#00d4ff', markersize=6, zorder=5)
    ax1.set_ylim(0, 1.1)
    ax1.set_xlim(theta_start - 0.05, theta_end + 0.05)
    ax1.set_axis_off()
    ax1.set_title(f"VVIX: {vvix_val:.1f}  (Pctl: {vvix_pct:.0f}%)", fontsize=10, fontweight="bold", pad=15, fontfamily="sans-serif", y=1.05)

    # Right: historical percentile bar
    ax2 = fig.add_subplot(122)
    pct_bars = [vvix_pct, 100 - vvix_pct]
    colors_bar = ["#00d4ff", "#1e2d4a"]
    ax2.barh([0], [pct_bars[0]], color=colors_bar[0], height=0.4, alpha=0.8)
    ax2.barh([0], [pct_bars[1]], left=[pct_bars[0]], color=colors_bar[1], height=0.4, alpha=0.8)
    ax2.set_xlim(0, 100)
    ax2.set_yticks([])
    ax2.set_xlabel("Percentile", fontsize=9, fontfamily="sans-serif")
    ax2.set_title("VVIX Historical Percentile", fontsize=10, fontweight="bold", fontfamily="sans-serif")
    ax2.axvline(vvix_pct, color="#ffaa00", linewidth=1.5, linestyle="--")
    ax2.text(vvix_pct + 2, 0, f"{vvix_pct:.0f}%", fontsize=10, color="#ffaa00", fontweight="bold", fontfamily="monospace")
    ax2.grid(True, alpha=0.2, axis="x")

    plt.tight_layout()
    return fig


def chart_vix_vvix_ratio(vix_series, vvix_series, ratio_z=None):
    """VIX/VVIX ratio chart with mean reversion signal."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), gridspec_kw={'height_ratios': [2, 1]}, sharex=False)

    min_len = min(len(vix_series), len(vvix_series))
    if min_len < 2:
        ax1.text(0.5, 0.5, "Insufficient data", ha='center', va='center', transform=ax1.transAxes, fontsize=12, color="#8899aa")
        return fig

    vix = vix_series.tail(60).values
    vvix = vvix_series.tail(60).values
    ratio = vix / vvix
    days = range(len(ratio))

    # Top: VIX and VVIX levels
    ax1.plot(days, vix, color="#00d4ff", linewidth=1.5, label="VIX")
    ax1_twin = ax1.twinx()
    ax1_twin.plot(days, vvix, color="#ffaa00", linewidth=1.5, label="VVIX", linestyle="--")
    ax1.set_ylabel("VIX", fontsize=9, color="#00d4ff", fontfamily="sans-serif")
    ax1_twin.set_ylabel("VVIX", fontsize=9, color="#ffaa00", fontfamily="sans-serif")
    ax1.set_title("VIX vs VVIX (60-day)", fontsize=11, fontweight="bold", fontfamily="sans-serif")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax1_twin.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8, framealpha=0.8)
    ax1.grid(True, alpha=0.3)

    # Bottom: VIX/VVIX ratio
    ax2.plot(days, ratio, color="#c8d6e5", linewidth=1.5)
    ax2.fill_between(days, ratio, np.mean(ratio), where=(ratio >= np.mean(ratio)), alpha=0.15, color="#00ff88")
    ax2.fill_between(days, ratio, np.mean(ratio), where=(ratio < np.mean(ratio)), alpha=0.15, color="#ff4444")
    ax2.axhline(np.mean(ratio), color="#ffaa00", linewidth=1, linestyle="--", label=f"Mean: {np.mean(ratio):.4f}")
    if ratio_z is not None:
        ax2.annotate(f"Z-Score: {ratio_z:+.2f}", xy=(0.98, 0.95), xycoords="axes fraction",
                    fontsize=9, color="#ffaa00", ha="right", va="top", fontfamily="monospace")
    ax2.set_ylabel("VIX/VVIX Ratio", fontsize=9, fontfamily="sans-serif")
    ax2.set_xlabel("Trading Days", fontsize=9, fontfamily="sans-serif")
    ax2.set_title("VIX/VVIX Ratio -- Mean Reversion Signal", fontsize=10, fontweight="bold", fontfamily="sans-serif")
    ax2.legend(fontsize=8, framealpha=0.8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    return fig


def chart_vol_regime_dashboard(regime_data):
    """Volatility regime detection dashboard."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

    # Left: regime probability bar chart
    probs = regime_data.get("probabilities", {})
    if probs:
        regimes = list(probs.keys())
        values = list(probs.values())
        colors = ["#00ff88", "#00d4ff", "#ffaa00", "#ff4444"]
        bars = ax1.barh(regimes, values, color=colors[:len(regimes)], alpha=0.7, height=0.5)
        for bar, val in zip(bars, values):
            ax1.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2,
                    f"{val:.1f}%", va='center', fontsize=9, fontfamily="monospace", color="#c8d6e5")
        ax1.set_xlim(0, max(values) * 1.3)
        ax1.set_xlabel("Probability (%)", fontsize=9, fontfamily="sans-serif")
        current = regime_data.get("regime", "Unknown")
        ax1.set_title(f"Regime Probabilities  [Current: {current}]", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax1.grid(True, alpha=0.2, axis="x")

    # Right: regime history (last 30 days)
    hist = regime_data.get("history", [])
    if hist:
        hdf = pd.DataFrame(hist)
        regime_map = {"Low Vol": 0, "Normal": 1, "Elevated": 2, "Crisis": 3}
        hdf["regime_num"] = hdf["regime"].map(regime_map)
        ax2.scatter(range(len(hdf)), hdf["vix"], c=hdf["regime_num"], cmap="RdYlGn_r", s=30, alpha=0.8, zorder=3)
        ax2.plot(range(len(hdf)), hdf["vix"], color="#2a3a5a", linewidth=0.5, zorder=2)
        ax2.set_ylabel("VIX", fontsize=9, fontfamily="sans-serif")
        ax2.set_xlabel("Days Ago", fontsize=9, fontfamily="sans-serif")
        ax2.set_title("Regime History (30-day VIX)", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax2.grid(True, alpha=0.2)
        ax2.invert_xaxis()

    plt.tight_layout()
    return fig


def chart_vrp_analysis(vrp_data):
    """Vol Risk Premium analysis chart."""
    if not vrp_data:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "Insufficient data for VRP analysis", ha='center', va='center', transform=ax.transAxes, fontsize=12, color="#8899aa")
        return fig

    n = len(vrp_data)
    fig, axes = plt.subplots(n, 1, figsize=(12, 3.5 * n), sharex=False)
    if n == 1:
        axes = [axes]

    for ax, (label, data) in zip(axes, vrp_data.items()):
        vrp_hist = data.get("vrp_history")
        if vrp_hist is not None and len(vrp_hist) > 10:
            v = vrp_hist.tail(60)
            ax.fill_between(range(len(v)), v.values, 0, where=(v.values >= 0), alpha=0.2, color="#00ff88", label="Positive VRP (IV > RV)")
            ax.fill_between(range(len(v)), v.values, 0, where=(v.values < 0), alpha=0.2, color="#ff4444", label="Negative VRP (IV < RV)")
            ax.plot(range(len(v)), v.values, color="#00d4ff", linewidth=1.2)
            ax.axhline(0, color="#2a3a5a", linewidth=1)
            ax.axhline(data["vrp_mean"], color="#ffaa00", linewidth=1, linestyle="--", label=f"Mean: {data['vrp_mean']:.2f}")
            ax.set_ylabel("VRP (vol pts)", fontsize=9, fontfamily="sans-serif")
            ax.set_title(f"Vol Risk Premium ({label})  --  Current: {data['vrp']:.2f}  |  Z-Score: {data['vrp_zscore']:+.2f}",
                        fontsize=10, fontweight="bold", fontfamily="sans-serif")
            ax.legend(fontsize=7, framealpha=0.8, loc="upper right")
            ax.grid(True, alpha=0.3)
        else:
            ax.text(0.5, 0.5, f"No history for {label}", ha='center', va='center', transform=ax.transAxes, fontsize=10, color="#8899aa")

    plt.tight_layout()
    return fig


def chart_vix_fear_gauge_enhanced(fear_data):
    """Enhanced VIX Fear Gauge combining VIX, VVIX, VIX/VVIX ratio, VRP."""
    fig = plt.figure(figsize=(12, 4))

    # Left: polar gauge
    ax1 = fig.add_subplot(121, projection='polar')
    val = fear_data.get("value", 50)
    zone = fear_data.get("zone", "Unknown")
    color = fear_data.get("color", "#8899aa")

    theta_start = np.pi * 0.15
    theta_end = np.pi * 0.85
    theta_range = theta_end - theta_start
    segments = [(0,25,"#00ff88","Complacent"),(25,45,"#88cc00","Low Fear"),(45,60,"#ffaa00","Moderate"),(60,80,"#ff6600","Elevated"),(80,100,"#ff4444","Extreme")]
    for lo, hi, seg_color, _ in segments:
        t_lo = theta_start + theta_range * lo / 100
        t_hi = theta_start + theta_range * hi / 100
        t_seg = np.linspace(t_lo, t_hi, 30)
        ax1.fill_between(t_seg, 0.6, 1.0, color=seg_color, alpha=0.25)
    needle_theta = theta_start + theta_range * val / 100
    ax1.annotate('', xy=(needle_theta, 0.85), xytext=(needle_theta, 0),
                arrowprops=dict(arrowstyle='->', color='#c8d6e5', lw=2.5))
    ax1.plot(needle_theta, 0, 'o', color=color, markersize=6, zorder=5)
    ax1.set_ylim(0, 1.1)
    ax1.set_xlim(theta_start - 0.05, theta_end + 0.05)
    ax1.set_axis_off()
    ax1.set_title(f"VIX Fear Gauge: {val:.0f}  [{zone}]", fontsize=10, fontweight="bold", pad=15, fontfamily="sans-serif", y=1.05)

    # Right: component breakdown
    ax2 = fig.add_subplot(122)
    components = fear_data.get("components", {})
    if components:
        names = list(components.keys())
        vals = list(components.values())
        max_vals = [40, 25, 15, 20]
        pcts = [v/m*100 if m > 0 else 0 for v, m in zip(vals, max_vals)]
        colors_bar = ["#00d4ff", "#ffaa00", "#ff6600", "#00ff88"]
        bars = ax2.barh(names, pcts, color=colors_bar[:len(names)], alpha=0.7, height=0.5)
        for bar, v, p in zip(bars, vals, pcts):
            ax2.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2,
                    f"{v:.1f} ({p:.0f}%)", va='center', fontsize=8, fontfamily="monospace", color="#c8d6e5")
        ax2.set_xlim(0, 110)
        ax2.set_xlabel("Contribution (%)", fontsize=9, fontfamily="sans-serif")
        ax2.set_title("Fear Index Components", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax2.grid(True, alpha=0.2, axis="x")

    plt.tight_layout()
    return fig


def chart_gex_vex_vgr(records, spot):
    """GEX/VEX/VGR dashboard."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    df = pd.DataFrame(records)
    if df.empty or "strike" not in df.columns:
        return fig
    df = df.sort_values("strike")
    strikes = df["strike"].values
    width = (strikes[1]-strikes[0])*0.7 if len(strikes)>1 else 5

    g_c = df.get("gamma_call", pd.Series(0, index=df.index)).values
    g_p = df.get("gamma_put", pd.Series(0, index=df.index)).values
    o_c = df.get("oi_call", pd.Series(0, index=df.index)).values
    o_p = df.get("oi_put", pd.Series(0, index=df.index)).values
    gex_call = g_c * o_c * spot**2 / 100
    gex_put = -g_p * o_p * spot**2 / 100
    net_gex = gex_call + gex_put
    colors_gex = ["#00ff88" if v >= 0 else "#ff4444" for v in net_gex]
    ax1.bar(strikes, net_gex/1e9, width=width, color=colors_gex, alpha=0.75)
    ax1.axvline(spot, color="#ffaa00", linewidth=1.5, linestyle="--", label=f"Spot: {spot:,.0f}")
    ax1.axhline(0, color="#2a3a5a", linewidth=1)
    ax1.set_ylabel("Net GEX ($B)", fontsize=9, fontfamily="sans-serif")
    ax1.set_title("Gamma Exposure (GEX) by Strike", fontsize=10, fontweight="bold", fontfamily="sans-serif")
    ax1.legend(fontsize=8, framealpha=0.8)
    ax1.grid(True, alpha=0.3, axis="y")

    vanna_c = df.get("vanna_call", pd.Series(0, index=df.index)).values
    vanna_p = df.get("vanna_put", pd.Series(0, index=df.index)).values
    gamma_c = g_c
    gamma_p = g_p
    oi_c = o_c
    oi_p = o_p
    vex = (vanna_c * oi_c + vanna_p * oi_p) * spot / 1e6
    gex_abs = np.abs(gamma_c * oi_c + gamma_p * oi_p) * spot**2 / 100 / 1e6
    vgr = np.where(gex_abs > 0, np.abs(vex) / gex_abs, 0)
    colors_vgr = ["#ff6600" if v > 1 else "#00d4ff" for v in vgr]
    ax2.bar(strikes, vgr, width=width, color=colors_vgr, alpha=0.75)
    ax2.axvline(spot, color="#ffaa00", linewidth=1.5, linestyle="--", label=f"Spot: {spot:,.0f}")
    ax2.axhline(1, color="#ff6600", linewidth=1, linestyle=":", label="VGR = 1 (Vanna = Gamma)")
    ax2.set_xlabel("Strike", fontsize=9, fontfamily="sans-serif")
    ax2.set_ylabel("VGR (Vanna/Gamma Ratio)", fontsize=9, fontfamily="sans-serif")
    ax2.set_title("Vanna/Gamma Ratio (VGR) by Strike  [VGR > 1 = Vanna Dominant]", fontsize=10, fontweight="bold", fontfamily="sans-serif")
    ax2.legend(fontsize=8, framealpha=0.8)
    ax2.grid(True, alpha=0.3, axis="y")
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:,.0f}"))

    plt.tight_layout()
    return fig


def chart_cross_asset_vol(cross_data):
    """Cross-asset vol correlation heatmap and dispersion."""
    corr = cross_data.get("correlation", pd.DataFrame())
    current = cross_data.get("current", {})
    dispersion = cross_data.get("dispersion", {})

    if corr.empty:
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(0.5, 0.5, "Insufficient data for cross-asset vol analysis", ha='center', va='center', transform=ax.transAxes, fontsize=12, color="#8899aa")
        return fig

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5), gridspec_kw={'width_ratios': [2, 1]})

    im = ax1.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax1.set_xticks(range(len(corr.columns)))
    ax1.set_yticks(range(len(corr.index)))
    ax1.set_xticklabels(corr.columns, rotation=45, ha="right", fontsize=8, fontfamily="sans-serif")
    ax1.set_yticklabels(corr.index, fontsize=8, fontfamily="sans-serif")
    for i in range(len(corr.index)):
        for j in range(len(corr.columns)):
            ax1.text(j, i, f"{corr.values[i,j]:.2f}", ha='center', va='center', fontsize=7,
                    color="#c8d6e5" if abs(corr.values[i,j]) < 0.7 else "#0a0e1a", fontfamily="monospace")
    ax1.set_title("Cross-Asset Vol Correlation (60-day returns)", fontsize=10, fontweight="bold", fontfamily="sans-serif")
    cbar = plt.colorbar(im, ax=ax1, fraction=0.046, pad=0.04)
    cbar.set_label("Correlation", fontsize=8, fontfamily="sans-serif")

    if current:
        names = list(current.keys())
        vals = list(current.values())
        colors_bar = []
        for v in vals:
            if v < 15: colors_bar.append("#00ff88")
            elif v < 25: colors_bar.append("#ffaa00")
            elif v < 35: colors_bar.append("#ff6600")
            else: colors_bar.append("#ff4444")
        ax2.barh(names, vals, color=colors_bar, alpha=0.7, height=0.5)
        ax2.set_xlabel("Current Level", fontsize=9, fontfamily="sans-serif")
        vol_of_vols = dispersion.get("vol_of_vols", 0)
        ax2.set_title(f"Current Vol Levels  [Dispersion: {vol_of_vols:.1f}%]", fontsize=10, fontweight="bold", fontfamily="sans-serif")
        ax2.grid(True, alpha=0.2, axis="x")

    plt.tight_layout()
    return fig


def chart_term_structure_heatmap(term_hist_data):
    """Historical term structure heatmap (time x tenor)."""
    if not term_hist_data or len(term_hist_data) < 5:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "Insufficient term structure history", ha='center', va='center', transform=ax.transAxes, fontsize=12, color="#8899aa")
        return fig

    fig, ax = plt.subplots(figsize=(12, 5))
    arr = np.array(term_hist_data["values"])
    tenors = term_hist_data.get("tenors", [f"T{i}" for i in range(arr.shape[1])])
    im = ax.imshow(arr.T, aspect="auto", origin="lower", cmap="YlOrRd", interpolation="bilinear")
    ax.set_yticks(range(len(tenors)))
    ax.set_yticklabels(tenors, fontsize=8, fontfamily="sans-serif")
    ax.set_xlabel("Trading Days Ago", fontsize=9, fontfamily="sans-serif")
    ax.set_title("VIX Term Structure History (Time x Tenor)", fontsize=11, fontweight="bold", fontfamily="sans-serif")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("VIX Level", fontsize=8, fontfamily="sans-serif")
    ax.grid(True, alpha=0.1)
    plt.tight_layout()
    return fig


def chart_regime_transition_matrix(trans_matrix):
    """Regime transition matrix heatmap."""
    if not trans_matrix:
        fig, ax = plt.subplots(figsize=(6, 5))
        ax.text(0.5, 0.5, "No transition matrix available", ha='center', va='center', transform=ax.transAxes, fontsize=12, color="#8899aa")
        return fig

    labels = list(trans_matrix.keys())
    n = len(labels)
    arr = np.array([[trans_matrix[r].get(c, 0) for c in labels] for r in labels])

    fig, ax = plt.subplots(figsize=(7, 5))
    im = ax.imshow(arr, cmap="YlGnBu", aspect="auto", vmin=0, vmax=100)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8, fontfamily="sans-serif")
    ax.set_yticklabels(labels, fontsize=8, fontfamily="sans-serif")
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{arr[i,j]:.1f}%", ha='center', va='center', fontsize=9,
                    color="#0a0e1a" if arr[i,j] > 50 else "#c8d6e5", fontfamily="monospace", fontweight="bold")
    ax.set_title("Regime Transition Matrix (%)", fontsize=11, fontweight="bold", fontfamily="sans-serif")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Transition Prob (%)", fontsize=8, fontfamily="sans-serif")
    plt.tight_layout()
    return fig


# =============================================================================
# LAYOUT
# =============================================================================

st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;
padding:8px 0 12px 0;border-bottom:1px solid #2a3a5a;margin-bottom:16px">
<div><span style="color:#00d4ff;font-size:1.1rem;font-weight:700;letter-spacing:0.04em;
font-family:sans-serif">MK QUANT MONITOR</span>
<span style="color:#8899aa;font-size:0.7rem;margin-left:12px;font-family:monospace">
INSTITUTIONAL TERMINAL v7.0 -- VOLATILITY VINCE EDITION</span></div>
<div style="color:#8899aa;font-size:0.7rem;font-family:monospace">
{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</div>
</div>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.06em;font-family:sans-serif'>CONTROLS</span>", unsafe_allow_html=True)
    st.checkbox("Force DEMO_MODE", value=DEMO_MODE)
    st.markdown("<hr style='border-color:#2a3a5a;margin:10px 0'>", unsafe_allow_html=True)
    st.markdown("<span style='color:#8899aa;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.06em;font-family:sans-serif'>Data Sources</span>", unsafe_allow_html=True)
    st.markdown("<span style='color:#c8d6e5;font-size:0.75rem;font-family:monospace'>CBOE  SPX options chain</span>", unsafe_allow_html=True)
    st.markdown("<span style='color:#c8d6e5;font-size:0.75rem;font-family:monospace'>Deribit  crypto perps + options</span>", unsafe_allow_html=True)
    st.markdown("<span style='color:#c8d6e5;font-size:0.75rem;font-family:monospace'>CoinGecko  top 50</span>", unsafe_allow_html=True)
    st.markdown("<span style='color:#c8d6e5;font-size:0.75rem;font-family:monospace'>Yahoo Finance + yfinance</span>", unsafe_allow_html=True)
    st.markdown("<span style='color:#c8d6e5;font-size:0.75rem;font-family:monospace'>Fear &amp; Greed Index</span>", unsafe_allow_html=True)
    st.markdown("<span style='color:#c8d6e5;font-size:0.75rem;font-family:monospace'>CongressInvests</span>", unsafe_allow_html=True)
    st.markdown("<hr style='border-color:#2a3a5a;margin:10px 0'>", unsafe_allow_html=True)
    st.markdown("<span style='color:#3a4a5a;font-size:0.65rem;font-family:monospace'>No API keys in source</span>", unsafe_allow_html=True)

tabs = st.tabs(["Markets Overview", "SPX & VIX Analytics", "Volatility Vince", "MarketGuardian Pro", "Crypto Ultra", "Insider Trades", "Settings"])

# ==============================================================================
# TAB 1: MARKETS OVERVIEW
# ==============================================================================
with tabs[0]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>MARKETS OVERVIEW -- Global Indices, Commodities &amp; Volatility</span>", unsafe_allow_html=True)
    data = fetch_yahoo()
    fg = fetch_fear_greed()

    if fg:
        fg_val = fg.get("value", 0)
        fg_label = fg.get("label", "Unknown")
        fg_color = "#00ff88" if fg_val > 60 else "#ffaa00" if fg_val > 40 else "#ff6600" if fg_val > 20 else "#ff4444"
        col_fg1, col_fg2 = st.columns([1, 2])
        with col_fg1:
            st.markdown(f"""<div style="background:#1a2332;border:1px solid {fg_color};border-radius:4px;padding:14px 18px">
<div style="color:#8899aa;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em;font-family:sans-serif">Fear &amp; Greed Index</div>
<div style="color:{fg_color};font-size:2.2rem;font-weight:700;font-family:monospace">{fg_val}</div>
<div style="color:{fg_color};font-size:0.85rem;font-family:sans-serif;font-weight:600">{fg_label}</div>
</div>""", unsafe_allow_html=True)
        with col_fg2:
            fig_fg = chart_fear_greed_gauge(fg_val, fg_label)
            st.pyplot(fig_fg)

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:16px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>GLOBAL INDICES</span>", unsafe_allow_html=True)

    INDICES = [
        {"key":"SPX","name":"S&P 500","vola":"VIX"},{"key":"NDX","name":"Nasdaq 100","vola":"VXN"},
        {"key":"DJX","name":"Dow Jones","vola":"VXD"},{"key":"RUT","name":"Russell 2000","vola":"RVX"},
        {"key":"FDAX1!","name":"DAX Future","vola":"EVIV"},{"key":"ESTX50","name":"Euro Stoxx 50","vola":"EVIV"},
        {"key":"NIKKEI","name":"Nikkei 225","vola":"VHSI"},{"key":"ES1!","name":"S&P 500 Fut","vola":"VIX"},
        {"key":"NQ1!","name":"Nasdaq 100 Fut","vola":"VXN"},
    ]
    idx_rows = []
    for idx in INDICES:
        s = _s(data.get(idx["key"]))
        vs = _s(data.get(idx["vola"]))
        if s is not None and len(s) > 0:
            price = s.iloc[-1]
            chg = ((s.iloc[-1]/s.iloc[-2])-1)*100 if len(s) > 1 else None
            high = s.tail(5).max(); low = s.tail(5).min()
            vol_price = vs.iloc[-1] if vs is not None and len(vs) > 0 else None
            vol_chg = ((vs.iloc[-1]/vs.iloc[-2])-1)*100 if vs is not None and len(vs) > 1 else None
            ivr = None
            if vs is not None and len(vs) > 60:
                mn, mx = vs.tail(60).min(), vs.tail(60).max()
                if mx > mn: ivr = (vol_price - mn) / (mx - mn) * 100
            idx_rows.append({"Ticker":idx["key"],"Name":idx["name"],"Price":price,"Chg%":chg,"High":high,"Low":low,"Vola":idx["vola"],"Vola Price":vol_price,"Vola Chg%":vol_chg,"IV Rank":ivr})

    if idx_rows:
        mc = st.columns(4)
        for i, row in enumerate(idx_rows[:4]):
            with mc[i]:
                chg_str = f"{row['Chg%']:+.2f}%" if row['Chg%'] is not None else "N/A"
                st.metric(row["Ticker"], f"{row['Price']:,.2f}", chg_str)
                vol_str = f"{row['Vola']}: {row['Vola Price']:.2f}" if row['Vola Price'] else f"{row['Vola']}: N/A"
                st.caption(vol_str)
        mc2 = st.columns(4)
        for i, row in enumerate(idx_rows[4:8]):
            with mc2[i]:
                chg_str = f"{row['Chg%']:+.2f}%" if row['Chg%'] is not None else "N/A"
                st.metric(row["Ticker"], f"{row['Price']:,.2f}", chg_str)
                vol_str = f"{row['Vola']}: {row['Vola Price']:.2f}" if row['Vola Price'] else f"{row['Vola']}: N/A"
                st.caption(vol_str)
        if len(idx_rows) > 8:
            mc3 = st.columns(len(idx_rows) - 8)
            for i, row in enumerate(idx_rows[8:]):
                with mc3[i]:
                    chg_str = f"{row['Chg%']:+.2f}%" if row['Chg%'] is not None else "N/A"
                    st.metric(row["Ticker"], f"{row['Price']:,.2f}", chg_str)
                    vol_str = f"{row['Vola']}: {row['Vola Price']:.2f}" if row['Vola Price'] else f"{row['Vola']}: N/A"
                    st.caption(vol_str)
        with st.expander("Detailed Index Data", expanded=False):
            display_df = pd.DataFrame(idx_rows)
            for col in ["Price","High","Low"]:
                display_df[col] = display_df[col].map(lambda x: f"{x:,.2f}" if x else "N/A")
            display_df["Chg%"] = display_df["Chg%"].map(lambda x: f"{x:+.2f}%" if x is not None else "N/A")
            display_df["Vola Price"] = display_df["Vola Price"].map(lambda x: f"{x:.2f}" if x else "N/A")
            display_df["Vola Chg%"] = display_df["Vola Chg%"].map(lambda x: f"{x:+.2f}%" if x is not None else "N/A")
            display_df["IV Rank"] = display_df["IV Rank"].map(lambda x: f"{x:.0f}%" if x is not None else "N/A")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:16px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>COMMODITIES</span>", unsafe_allow_html=True)
    COMMODITIES = [
        {"key":"GC1!","name":"Gold Future","vola":"GVZ"},{"key":"SI1!","name":"Silver Future","vola":"VXSLV"},
        {"key":"HG1!","name":"Copper Future","vola":None},{"key":"CL1!","name":"Crude Oil WTI","vola":"OVX"},
        {"key":"BZ1!","name":"Brent Oil Future","vola":"OVX"},{"key":"NG1!","name":"Natural Gas","vola":None},
    ]
    comm_rows = []
    for comm in COMMODITIES:
        s = _s(data.get(comm["key"]))
        vs = _s(data.get(comm["vola"])) if comm.get("vola") else None
        if s is not None and len(s) > 0:
            price = s.iloc[-1]; chg = ((s.iloc[-1]/s.iloc[-2])-1)*100 if len(s) > 1 else None
            high = s.tail(5).max(); low = s.tail(5).min()
            vol_price = vs.iloc[-1] if vs is not None and len(vs) > 0 else None
            vol_chg = ((vs.iloc[-1]/vs.iloc[-2])-1)*100 if vs is not None and len(vs) > 1 else None
            comm_rows.append({"Ticker":comm["key"],"Name":comm["name"],"Price":price,"Chg%":chg,"High":high,"Low":low,"Vola":comm.get("vola","--"),"Vola Price":vol_price,"Vola Chg%":vol_chg})
    if comm_rows:
        mc = st.columns(len(comm_rows))
        for i, row in enumerate(comm_rows):
            with mc[i]:
                chg_str = f"{row['Chg%']:+.2f}%" if row['Chg%'] is not None else "N/A"
                st.metric(row["Ticker"], f"{row['Price']:,.2f}", chg_str)
                if row['Vola Price']:
                    st.caption(f"{row['Vola']}: {row['Vola Price']:.2f} ({row['Vola Chg%']:+.2f}%)" if row['Vola Chg%'] else f"{row['Vola']}: {row['Vola Price']:.2f}")
                else:
                    st.caption(f"Vola: {row['Vola']}")
        with st.expander("Detailed Commodity Data", expanded=False):
            display_df = pd.DataFrame(comm_rows)
            for col in ["Price","High","Low"]:
                display_df[col] = display_df[col].map(lambda x: f"{x:,.2f}" if x else "N/A")
            display_df["Chg%"] = display_df["Chg%"].map(lambda x: f"{x:+.2f}%" if x is not None else "N/A")
            display_df["Vola Price"] = display_df["Vola Price"].map(lambda x: f"{x:.2f}" if x else "N/A")
            display_df["Vola Chg%"] = display_df["Vola Chg%"].map(lambda x: f"{x:+.2f}%" if x is not None else "N/A")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:16px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VOLATILITY MONITOR</span>", unsafe_allow_html=True)
    vol_indices = ["VIX","VVIX","VXN","VXD","RVX","OVX","GVZ","VXEEM"]
    vol_data = {}
    for vk in vol_indices:
        s = _s(data.get(vk))
        if s is not None and len(s) > 0:
            val = s.iloc[-1]; chg = ((s.iloc[-1]/s.iloc[-2])-1)*100 if len(s) > 1 else None
            ivr = None
            if len(s) > 60:
                mn, mx = s.tail(60).min(), s.tail(60).max()
                if mx > mn: ivr = (val - mn) / (mx - mn) * 100
            vol_data[vk] = {"value": val, "change": chg, "ivr": ivr}
    if vol_data:
        fig_vol = chart_vol_heatmap(vol_data)
        st.pyplot(fig_vol)

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:16px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VIX TERM STRUCTURE</span>", unsafe_allow_html=True)
    vix_terms = {}
    vix_symbols_ts = {"Spot VIX":"VIX","VIX 1M":"^VIX3M","VIX 2M":"^VIX6M"}
    for name, key in vix_symbols_ts.items():
        s = _s(data.get(key))
        if s is not None and len(s) > 0:
            vix_terms[name] = s.iloc[-1]
    if len(vix_terms) >= 2:
        col1, col2 = st.columns(2)
        with col1:
            fig_ts = chart_term_structure(vix_terms)
            st.pyplot(fig_ts)
        with col2:
            st.markdown("<span style='color:#8899aa;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.06em;font-family:sans-serif'>Term Structure Levels</span>", unsafe_allow_html=True)
            for name, val in vix_terms.items():
                st.markdown(f"<div style='color:#c8d6e5;font-family:monospace;font-size:0.85rem;padding:2px 0'>{name}: <span style='color:#00d4ff'>{val:.2f}</span></div>", unsafe_allow_html=True)
            vals = list(vix_terms.values())
            if len(vals) >= 2:
                spread = vals[1] - vals[0]
                regime = "CONTANGO" if spread > 0 else "BACKWARDATION"
                regime_color = "#00ff88" if spread > 0 else "#ff4444"
                st.markdown(f"<div style='color:{regime_color};font-family:monospace;font-size:0.85rem;padding:4px 0'>Spread: <b>{spread:+.2f}</b>  [{regime}]</div>", unsafe_allow_html=True)
                st.caption("Contango = futures above spot (normal)" if spread > 0 else "Backwardation = futures below spot (stress signal)")
    else:
        st.info("VIX futures data unavailable -- Yahoo Finance may be rate-limited")

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:16px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>MACRO INDICATORS (FRED)</span>", unsafe_allow_html=True)
    fred_s = {"DGS10":"10Y Treasury","T10Y2Y":"10Y-2Y Spread","DFF":"Fed Funds Rate","T5YIE":"5Y Breakeven"}
    fc = st.columns(len(fred_s))
    for i,(sid,label) in enumerate(fred_s.items()):
        with fc[i]:
            fd = fetch_fred(sid, 1)
            if fd is not None and len(fd)>0:
                st.metric(label, f"{fd.iloc[-1]:.2f}%")
            else:
                st.metric(label, "N/A", "Set FRED_API_KEY")

# ==============================================================================
# TAB 2: SPX & VIX ANALYTICS
# ==============================================================================
with tabs[1]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>SPX &amp; VIX ANALYTICS -- Options Chain Analysis</span>", unsafe_allow_html=True)
    st.caption("Live CBOE data | GEX+/VEX/VGR | Zero Gamma | Crash Profile | BL Forecast | IV Skew/Smile | Heatmap")

    with st.spinner("Fetching SPX options chain..."):
        chain = fetch_cboe_spx_chain()

    if chain and chain.get("records"):
        spot = chain["spot"]
        records = chain["records"]
        source = chain.get("source","unknown")
        ts = chain.get("timestamp","")
        st.markdown(f"<div style='background:#1a2332;border-left:3px solid #00ff88;border-radius:2px;padding:8px 14px;margin:8px 0'><span style='color:#00ff88;font-family:monospace;font-size:0.8rem'>LIVE</span> <span style='color:#c8d6e5;font-family:monospace;font-size:0.8rem'>CBOE data | Spot: {spot:,.2f} | {len(records)} strikes | Source: {source} | {ts[:19]}</span></div>", unsafe_allow_html=True)
    else:
        # Generate realistic synthetic data based on current VIX/VVIX from Yahoo
        vix_data = fetch_yahoo()
        vix_s = _s(vix_data.get("VIX"))
        vvix_s = _s(vix_data.get("VVIX"))
        spx_s = _s(vix_data.get("SPX"))
        
        # Use real VIX/VVIX if available, otherwise defaults
        current_vix = vix_s.iloc[-1] if vix_s is not None and len(vix_s) > 0 else 18.0
        current_vvix = vvix_s.iloc[-1] if vvix_s is not None and len(vvix_s) > 0 else 85.0
        spot = spx_s.iloc[-1] if spx_s is not None and len(spx_s) > 0 else 5450.0
        
        # Derive ATM IV from VIX (VIX ~= ATM IV * 100 for SPX)
        atm_iv = current_vix / 100.0
        
        rng = np.random.default_rng(int(spot) % 10000)
        records = []
        
        # Generate strikes around spot (80% to 120% of spot)
        strike_range = int(spot * 0.20 / 25) * 25
        strikes = np.arange(spot - strike_range, spot + strike_range + 25, 25)
        
        # Generate multiple expiries (weekly + monthly)
        from datetime import timedelta
        today = datetime.utcnow().date()
        expiries = []
        # Weekly expiries (next 4 Fridays)
        for i in range(1, 5):
            d = today + timedelta(weeks=i)
            # Adjust to Friday
            while d.weekday() != 4:
                d += timedelta(days=1)
            expiries.append(d.strftime("%Y-%m-%d"))
        # Monthly expiries (next 3 months)
        for i in range(1, 4):
            m = today.month + i
            y = today.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            d = datetime(y, m, 1).date()
            # Third Friday
            while d.weekday() != 4:
                d += timedelta(days=1)
            d += timedelta(weeks=2)
            expiries.append(d.strftime("%Y-%m-%d"))
        
        for exp in expiries:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte = max(1, (exp_date - today).days)
            except:
                dte = 30
            T = dte / 365.0
            
            for k in strikes:
                atm_dist = (k - spot) / spot
                
                # Realistic IV skew: higher for OTM puts, lower for OTM calls
                # VVIX influences the skew steepness
                skew_factor = current_vvix / 85.0  # Normalize around typical VVIX
                iv_c = max(0.03, atm_iv + abs(atm_dist) * 0.25 * skew_factor - atm_dist * 0.08 * skew_factor)
                iv_p = max(0.03, iv_c + 0.015 + max(0.0, -atm_dist * 0.12 * skew_factor))
                
                # Gamma: peaks ATM, decays with distance
                gamma_base = 0.004 * np.exp(-60 * atm_dist**2) / np.sqrt(T)
                gamma_c = max(0.000001, gamma_base * (1 + rng.normal(0, 0.05)))
                gamma_p = max(0.000001, gamma_base * (1 + rng.normal(0, 0.05)))
                
                # Delta: based on Black-Scholes approximation
                if T > 0 and iv_c > 0:
                    d1_c = (np.log(spot/k) + (0.5 * iv_c**2) * T) / (iv_c * np.sqrt(T))
                    delta_c = float(np.clip(0.5 + d1_c * 0.4, 0.01, 0.99))
                else:
                    delta_c = 0.5 if k <= spot else 0.01
                delta_p = delta_c - 1.0
                
                # Vanna: peaks at wings
                vanna_c = float(gamma_c * (1 - delta_c) / max(iv_c, 0.01) * atm_dist)
                vanna_p = float(gamma_p * (1 - abs(delta_p)) / max(iv_p, 0.01) * atm_dist)
                
                # OI: higher ATM, lower wings, higher for puts (hedging demand)
                oi_base = abs(rng.normal(6000, 2500))
                oi_factor = np.exp(-25 * atm_dist**2) + 0.05
                oi_call = int(oi_base * oi_factor * (1 + rng.normal(0, 0.1)))
                oi_put = int(oi_base * oi_factor * 1.3 * (1 + rng.normal(0, 0.1)))
                
                # Theta
                theta_c = -gamma_c * spot * spot * iv_c / (2 * np.sqrt(T)) / 365 if T > 0 else 0
                theta_p = -gamma_p * spot * spot * iv_p / (2 * np.sqrt(T)) / 365 if T > 0 else 0
                
                # Vega
                vega_val = spot * np.sqrt(T) * np.exp(-0.5 * (np.log(spot/k)/max(iv_c,0.01))**2) * 0.01 if T > 0 and iv_c > 0 else 0
                
                # Bid/ask
                intrinsic_c = max(0, spot - k)
                time_value_c = max(0.01, iv_c * spot * np.sqrt(T) * 0.4)
                mid_c = intrinsic_c + time_value_c
                spread_c = max(0.25, mid_c * 0.03)
                
                intrinsic_p = max(0, k - spot)
                time_value_p = max(0.01, iv_p * spot * np.sqrt(T) * 0.4)
                mid_p = intrinsic_p + time_value_p
                spread_p = max(0.25, mid_p * 0.03)
                
                records.append({
                    "strike": float(k),
                    "expiry": exp,
                    "oi_call": max(0, oi_call),
                    "oi_put": max(0, oi_put),
                    "iv_call": round(iv_c, 4),
                    "iv_put": round(iv_p, 4),
                    "delta_call": round(delta_c, 4),
                    "delta_put": round(delta_p, 4),
                    "gamma_call": round(gamma_c, 6),
                    "gamma_put": round(gamma_p, 6),
                    "theta_call": round(theta_c, 4),
                    "theta_put": round(theta_p, 4),
                    "vega_call": round(vega_val, 4),
                    "vega_put": round(vega_val, 4),
                    "vanna_call": round(vanna_c, 4),
                    "vanna_put": round(vanna_p, 4),
                    "bid_call": round(max(0.01, mid_c - spread_c/2), 2),
                    "ask_call": round(mid_c + spread_c/2, 2),
                    "bid_put": round(max(0.01, mid_p - spread_p/2), 2),
                    "ask_put": round(mid_p + spread_p/2, 2),
                })
        
        st.markdown(f"<div style='background:#1a2332;border-left:3px solid #ffaa00;border-radius:2px;padding:8px 14px;margin:8px 0'><span style='color:#ffaa00;font-family:monospace;font-size:0.8rem'>SYNTHETIC</span> <span style='color:#c8d6e5;font-family:monospace;font-size:0.8rem'>CBOE unreachable | VIX: {current_vix:.1f} | VVIX: {current_vvix:.1f} | {len(records)} records | {len(expiries)} expiries</span></div>", unsafe_allow_html=True)

    strikes_list = records
    gex_val = compute_gex_plus(strikes_list, spot)
    vex_val = compute_vanna_exposure(strikes_list, spot)
    vgr_val = abs(vex_val) / abs(gex_val) if abs(gex_val) > 1e-6 else 0.0
    zg_val = find_zero_gamma(strikes_list, spot)

    vix_data = fetch_yahoo()
    vix_s = _s(vix_data.get("VIX"))
    vix_val = vix_s.iloc[-1] if vix_s is not None and len(vix_s)>0 else None
    vvix_s = _s(vix_data.get("VVIX"))
    vvix_val = vvix_s.iloc[-1] if vvix_s is not None and len(vvix_s)>0 else None

    # Max pain and gamma walls
    max_pain_strike, _ = compute_max_pain(records, spot)
    gamma_walls = compute_gamma_walls(records, spot)
    delta_neutral = compute_delta_neutral_strike(records, spot)

    mcols = st.columns(6)
    with mcols[0]: st.metric("SPX Spot", f"{spot:,.2f}")
    with mcols[1]:
        if vix_val:
            st.metric("VIX", f"{vix_val:.2f}", f"{((vix_val/vix_s.iloc[-2])-1)*100:+.2f}%" if len(vix_s)>1 else None)
        else: st.metric("VIX", "N/A")
    with mcols[2]: st.metric("VVIX", f"{vvix_val:.2f}" if vvix_val else "N/A")
    with mcols[3]: st.metric("Net GEX+", f"${gex_val/1e9:.2f}B", "Long Gamma" if gex_val>0 else "Short Gamma")
    with mcols[4]: st.metric("Zero Gamma", f"{zg_val:,.0f}", f"{(zg_val/spot-1)*100:+.1f}%")
    with mcols[5]: st.metric("VGR", f"{vgr_val:.2f}", "Vanna Dominant" if vgr_val>1 else "Gamma Dominant")

    # Key levels row
    kl_cols = st.columns(4)
    with kl_cols[0]:
        st.metric("Max Pain", f"{max_pain_strike:,.0f}" if max_pain_strike else "N/A",
                 f"{(max_pain_strike/spot-1)*100:+.1f}%" if max_pain_strike else None)
    with kl_cols[1]:
        st.metric("Delta Neutral", f"{delta_neutral:,.0f}" if delta_neutral else "N/A",
                 f"{(delta_neutral/spot-1)*100:+.1f}%" if delta_neutral else None)
    with kl_cols[2]:
        if gamma_walls:
            top_wall = gamma_walls[0]
            st.metric("Top Gamma Wall", f"{top_wall['strike']:,.0f}", f"GEX: ${top_wall['gex']/1e9:.2f}B")
        else:
            st.metric("Top Gamma Wall", "N/A")
    with kl_cols[3]:
        st.metric("VEX", f"${vex_val/1e6:.1f}M")

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>GEX+ Crash Profile</span>", unsafe_allow_html=True)
        profile = compute_crash_profile(strikes_list, spot)
        st.pyplot(chart_gex_profile(profile, spot, zg_val))
    with c2:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>Open Interest by Strike</span>", unsafe_allow_html=True)
        st.pyplot(chart_oi_by_strike(records, spot))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>Gamma Exposure by Strike</span>", unsafe_allow_html=True)
        st.pyplot(chart_gex_by_strike(records, spot))
    with c2:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>IV Skew</span>", unsafe_allow_html=True)
        st.pyplot(chart_iv_skew(records, spot))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>Volatility Smile</span>", unsafe_allow_html=True)
        st.pyplot(chart_vol_smile(records, spot))
    with c2:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>GEX+ Heatmap (Spot vs IV Shock)</span>", unsafe_allow_html=True)
        hm = _compute_heatmap_inline(strikes_list, spot, n_spot=30, n_iv=40)
        st.pyplot(chart_heatmap(hm))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>Put/Call Ratios by Expiry</span>", unsafe_allow_html=True)
        st.pyplot(chart_pc_ratios(records))
    with c2:
        st.markdown("<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>GEX/VEX/VGR Dashboard</span>", unsafe_allow_html=True)
        st.pyplot(chart_gex_vex_vgr(records, spot))

    # BL Forecast
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>BREEDEN-LITZENBERGER FORECAST</span>", unsafe_allow_html=True)
    fc_bl = _compute_bl_forecast_inline(strikes_list, spot, dte=30)
    fcc = st.columns(2)
    for i,(period,label) in enumerate([("one_day","1-Day"),("one_week","1-Week")]):
        with fcc[i]:
            f = fc_bl[period]
            st.markdown(f"<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>{label} Forecast</span>", unsafe_allow_html=True)
            fc_cols = st.columns(4)
            with fc_cols[0]: st.metric("Sigma", f"{f['sigma_pct']:.1f}%")
            with fc_cols[1]: st.metric("Median", f"{f['median']:,.0f}")
            with fc_cols[2]: st.metric("5th pct", f"{f['p5']:,.0f}")
            with fc_cols[3]: st.metric("95th pct", f"{f['p95']:,.0f}")
            st.caption(f"90% Range: {f['range_90'][0]:,.0f} -- {f['range_90'][1]:,.0f}")
    st.pyplot(chart_bl_forecast(fc_bl["one_day"], fc_bl["one_week"]))

    # Options Chain Table
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>OPTIONS CHAIN DATA</span>", unsafe_allow_html=True)
    chain_df = pd.DataFrame(records).sort_values("strike")
    display_cols = ["strike","oi_call","oi_put","iv_call","iv_put","delta_call","delta_put","gamma_call","gamma_put"]
    available_cols = [c for c in display_cols if c in chain_df.columns]
    st.dataframe(chain_df[available_cols].head(30), use_container_width=True, height=400)

# =============================================================================
# TAB 3: VOLATILITY VINCE DASHBOARD
# =============================================================================
with tabs[2]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VOLATILITY VINCE -- Institutional Vol Intelligence</span>", unsafe_allow_html=True)
    st.caption("VVIX | VIX Term Structure | Vol Regime | VRP | Fear Gauge | Cross-Asset Vol | GEX/VEX/VGR")

    vd = fetch_yahoo()
    vix_s = _s(vd.get("VIX"))
    vvix_s = _s(vd.get("VVIX"))
    spx_s = _s(vd.get("SPX"))

    vix_cur = vix_s.iloc[-1] if vix_s is not None and len(vix_s) > 0 else None
    vvix_cur = vvix_s.iloc[-1] if vvix_s is not None and len(vvix_s) > 0 else None
    spx_cur = spx_s.iloc[-1] if spx_s is not None and len(spx_s) > 0 else None

    # ===================================================================
    # ROW 1: VVIX INTELLIGENCE
    # ===================================================================
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VVIX INTELLIGENCE (Volatility of Volatility)</span>", unsafe_allow_html=True)

    vvix_val, vvix_pct, vvix_rank = compute_vvix_percentile(vvix_s) if vvix_s is not None else (None, None, None)
    vix_vvix_ratio, ratio_z, ratio_hist = compute_vix_vvix_ratio(vix_s, vvix_s) if vix_s is not None and vvix_s is not None else (None, None, None)

    mcols_vvix = st.columns(4)
    with mcols_vvix[0]:
        st.metric("VVIX", f"{vvix_cur:.2f}" if vvix_cur else "N/A",
                  f"Chg: {((vvix_s.iloc[-1]/vvix_s.iloc[-2])-1)*100:+.2f}%" if vvix_s is not None and len(vvix_s) > 1 else None)
    with mcols_vvix[1]:
        st.metric("VVIX Percentile", f"{vvix_pct:.0f}%" if vvix_pct else "N/A",
                  f"Rank: {vvix_rank:.0f}/100" if vvix_rank else None)
    with mcols_vvix[2]:
        st.metric("VIX/VVIX Ratio", f"{vix_vvix_ratio:.4f}" if vix_vvix_ratio else "N/A",
                  f"Mean-Reversion Signal" if vix_vvix_ratio and ratio_z and abs(ratio_z) > 1.5 else "Normal")
    with mcols_vvix[3]:
        st.metric("Ratio Z-Score", f"{ratio_z:+.2f}" if ratio_z else "N/A",
                  f"{'Overvalued VIX' if ratio_z and ratio_z > 1 else 'Undervalued VIX' if ratio_z and ratio_z < -1 else 'Neutral'}" if ratio_z else None)

    c1, c2 = st.columns(2)
    with c1:
        if vvix_cur is not None and vvix_pct is not None:
            st.pyplot(chart_vvix_gauge(vvix_cur, vvix_pct))
    with c2:
        if vix_s is not None and vvix_s is not None:
            st.pyplot(chart_vix_vvix_ratio(vix_s, vvix_s, ratio_z))

    # ===================================================================
    # ROW 2: VIX TERM STRUCTURE (ENHANCED)
    # ===================================================================
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VIX TERM STRUCTURE (ENHANCED)</span>", unsafe_allow_html=True)

    vix_terms = {}
    vix_ts_keys = {"Spot": "VIX", "1M": "^VIX3M", "2M": "^VIX6M", "3M": "^VIX9M"}
    for name, key in vix_ts_keys.items():
        s = _s(vd.get(key))
        if s is not None and len(s) > 0:
            vix_terms[name] = s.iloc[-1]

    ts_metrics = compute_term_structure_metrics(vix_terms)

    mcols_ts = st.columns(5)
    with mcols_ts[0]:
        regime = ts_metrics.get("regime", "N/A")
        regime_color = "#00ff88" if "CONTANGO" in regime else "#ff4444"
        st.markdown(f"<div style='background:#1a2332;border:1px solid {regime_color};border-radius:4px;padding:8px 12px;text-align:center'><div style='color:#8899aa;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em'>Regime</div><div style='color:{regime_color};font-size:1.4rem;font-weight:700;font-family:monospace'>{regime}</div></div>", unsafe_allow_html=True)
    with mcols_ts[1]:
        spread = ts_metrics.get("spread", 0)
        st.metric("Front-Back Spread", f"{spread:+.2f}")
    with mcols_ts[2]:
        st.metric("Slope", f"{ts_metrics.get('slope', 0):.3f}",
                  f"{'Steep' if abs(ts_metrics.get('slope', 0)) > 0.5 else 'Flat'}" if ts_metrics.get('slope') else None)
    with mcols_ts[3]:
        st.metric("Curvature", f"{ts_metrics.get('curvature', 0):.3f}")
    with mcols_ts[4]:
        st.metric("Roll Yield", f"{ts_metrics.get('roll_yield', 0):.1f}%",
                  f"{'Positive' if ts_metrics.get('roll_yield', 0) > 0 else 'Negative'}")

    if len(vix_terms) >= 2:
        st.pyplot(chart_term_structure(vix_terms))

    # VIX Term Structure Historical Heatmap
    if len(vix_ts_keys) >= 2:
        vix_ts_hist = {}
        for name, key in vix_ts_keys.items():
            s = _s(vd.get(key))
            if s is not None and len(s) > 30:
                vix_ts_hist[name] = s.tail(60)
        if len(vix_ts_hist) >= 2:
            st.pyplot(chart_term_structure_heatmap(vix_ts_hist))

    # ===================================================================
    # ROW 3: VOLATILITY REGIME DETECTION
    # ===================================================================
    st.markdown("<div style='height='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VOLATILITY REGIME DETECTION</span>", unsafe_allow_html=True)

    regime_data = compute_vol_regime_detection(vix_s, vvix_s, spx_s)

    probs = regime_data.get("probabilities", {})
    current_regime = regime_data.get("regime", "N/A")
    regime_colors = {"Low Vol": "#00ff88", "Normal": "#88cc00", "Elevated": "#ffaa00", "Crisis": "#ff4444"}
    rc = regime_colors.get(current_regime, "#8899aa")

    mcols_reg = st.columns(5)
    with mcols_reg[0]:
        st.markdown(f"<div style='background:#1a2332;border:2px solid {rc};border-radius:4px;padding:8px 12px;text-align:center'><div style='color:#8899aa;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em'>Current Regime</div><div style='color:{rc};font-size:1.2rem;font-weight:700'>{current_regime}</div></div>", unsafe_allow_html=True)
    with mcols_reg[1]:
        st.metric("Low Vol Prob", f"{probs.get('Low Vol', 0):.1f}%")
    with mcols_reg[2]:
        st.metric("Normal Prob", f"{probs.get('Normal', 0):.1f}%")
    with mcols_reg[3]:
        st.metric("Elevated Prob", f"{probs.get('Elevated', 0):.1f}%")
    with mcols_reg[4]:
        st.metric("Crisis Prob", f"{probs.get('Crisis', 0):.1f}%")

    c1, c2 = st.columns(2)
    with c1:
        st.pyplot(chart_vol_regime_dashboard(regime_data))
    with c2:
        trans_matrix = regime_data.get("transition_matrix", {})
        if trans_matrix:
            st.pyplot(chart_regime_transition_matrix(trans_matrix))

    # ===================================================================
    # ROW 4: VOLATILITY RISK PREMIUM
    # ===================================================================
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VOLATILITY RISK PREMIUM (IV vs RV)</span>", unsafe_allow_html=True)

    vrp_data = compute_vol_risk_premium(vix_s, spx_s, windows=[20, 60, 120])

    if vrp_data:
        mcols_vrp = st.columns(len(vrp_data))
        for i, (label, vrp) in enumerate(vrp_data.items()):
            with mcols_vrp[i]:
                vrp_val = vrp['vrp']
                z = vrp['vrp_zscore']
                color = "#00ff88" if vrp_val > 0 else "#ff4444"
                st.markdown(f"<div style='background:#1a2332;border:1px solid {color};border-radius:4px;padding:8px 12px'><div style='color:#8899aa;font-size:0.65rem;text-transform:uppercase'>VRP ({label})</div><div style='color:{color};font-size:1.3rem;font-weight:700;font-family:monospace'>{vrp_val:+.2f}</div><div style='color:#8899aa;font-size:0.7rem'>Z-Score: {z:+.2f}</div><div style='color:#8899aa;font-size:0.65rem'>VIX: {vrp['vix']:.2f} | RV: {vrp['rv']:.2f}</div></div>", unsafe_allow_html=True)
        st.pyplot(chart_vrp_analysis(vrp_data))
    else:
        st.info("Insufficient data for VRP calculation")

    # ===================================================================
    # ROW 5: VIX FEAR GAUGE (COMPOSITE)
    # ===================================================================
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>VIX FEAR GAUGE (COMPOSITE INDEX)</span>", unsafe_allow_html=True)

    current_vrp_val = list(vrp_data.values())[0]["vrp"] if vrp_data else None
    fear_data = compute_vix_fear_gauge(vix_cur, vvix_cur, vix_vvix_ratio, current_vrp_val)

    fi = fear_data.get('value', 0)
    fz = fear_data.get('zone', 'Neutral')
    fc = "#ff4444" if fi > 75 else "#ffaa00" if fi > 50 else "#88cc00" if fi > 25 else "#00ff88"
    components = fear_data.get('components', {})

    mcols_fear = st.columns(4)
    with mcols_fear[0]:
        st.markdown(f"<div style='background:#1a2332;border:2px solid {fc};border-radius:4px;padding:8px 12px;text-align:center'><div style='color:#8899aa;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em'>Fear Index</div><div style='color:{fc};font-size:1.8rem;font-weight:700;font-family:monospace'>{fi:.0f}</div><div style='color:{fc};font-size:0.8rem;font-weight:600'>{fz}</div><div style='color:#8899aa;font-size:0.6rem'>/ 100</div></div>", unsafe_allow_html=True)
    with mcols_fear[1]:
        st.metric("VIX Component", f"{components.get('VIX', 0):.1f}/40",
                  f"Percentile: {fear_data.get('components', {}).get('VIX_pct', 0):.0f}%" if fear_data.get('components', {}).get('VIX_pct') else None)
    with mcols_fear[2]:
        st.metric("VVIX Component", f"{components.get('VVIX', 0):.1f}/25",
                  f"Percentile: {fear_data.get('components', {}).get('VVIX_pct', 0):.0f}%" if fear_data.get('components', {}).get('VVIX_pct') else None)
    with mcols_fear[3]:
        st.metric("VRP Component", f"{components.get('VRP', 0):.1f}/20",
                  f"Z-Score: {fear_data.get('components', {}).get('VRP_z', 0):+.2f}" if fear_data.get('components', {}).get('VRP_z') is not None else None)

    st.pyplot(chart_vix_fear_gauge_enhanced(fear_data))

    # ===================================================================
    # ROW 6: CROSS-ASSET VOLATILITY MONITOR
    # ===================================================================
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>CROSS-ASSET VOLATILITY MONITOR</span>", unsafe_allow_html=True)

    vol_names = ["VIX", "VVIX", "VXN", "VXD", "RVX", "OVX", "GVZ", "VXEEM"]
    vol_series_dict = {}
    for vn in vol_names:
        s = _s(vd.get(vn))
        if s is not None and len(s) > 20:
            vol_series_dict[vn] = s

    cross_data = compute_cross_asset_vol(vol_series_dict)

    if cross_data.get("current"):
        n_cols = min(len(cross_data["current"]), 8)
        mcols_cross = st.columns(n_cols)
        for i, (name, val) in enumerate(list(cross_data["current"].items())[:n_cols]):
            if i < n_cols:
                with mcols_cross[i]:
                    chg_str = ""
                    if name in vol_series_dict and len(vol_series_dict[name]) > 1:
                        chg = ((vol_series_dict[name].iloc[-1] / vol_series_dict[name].iloc[-2]) - 1) * 100
                        chg_str = f"{chg:+.2f}%"
                    st.metric(name, f"{val:.2f}", chg_str)

        disp = cross_data.get("dispersion", {}).get("vol_of_vols", 0)
        st.caption(f"Vol of Vols (cross-asset dispersion): {disp:.1f}% | Higher = more disagreement between asset vols")
        st.pyplot(chart_cross_asset_vol(cross_data))

    # ===================================================================
    # ROW 7: GEX/VEX/VGR DASHBOARD
    # ===================================================================
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>GEX/VEX/VGR EXPOSURE DASHBOARD</span>", unsafe_allow_html=True)

    # Use the SPX chain from Tab 2 if available, otherwise use synthetic
    try:
        chain_vv = fetch_cboe_spx_chain()
        if chain_vv and chain_vv.get("records"):
            spot_vv = chain_vv["spot"]
            records_vv = chain_vv["records"]
        else:
            raise Exception("No CBOE data")
    except:
        # Use synthetic based on current VIX
        spot_vv = spx_cur if spx_cur else 5450.0
        vix_for_synth = vix_cur if vix_cur else 18.0
        vvix_for_synth = vvix_cur if vvix_cur else 85.0
        rng = np.random.default_rng(42)
        records_vv = []
        strikes = np.arange(spot_vv * 0.85, spot_vv * 1.15 + 25, 25)
        atm_iv = vix_for_synth / 100.0
        skew_factor = vvix_for_synth / 85.0
        T = 30.0 / 365.0  # Default 30 DTE for synthetic
        for k in strikes:
            atm_dist = (k - spot_vv) / spot_vv
            iv_c = max(0.03, atm_iv + abs(atm_dist) * 0.25 * skew_factor - atm_dist * 0.08 * skew_factor)
            iv_p = max(0.03, iv_c + 0.015 + max(0.0, -atm_dist * 0.12 * skew_factor))
            gamma = 0.004 * np.exp(-60 * atm_dist**2)
            if T > 0 and iv_c > 0:
                d1 = (np.log(spot_vv/k) + (0.5 * iv_c**2) * 30/365) / (iv_c * np.sqrt(30/365))
                dc = float(np.clip(0.5 + d1 * 0.4, 0.01, 0.99))
            else:
                dc = 0.5 if k <= spot_vv else 0.01
            oi_c = int(abs(rng.normal(6000, 2500)) * (np.exp(-25 * atm_dist**2) + 0.05))
            oi_p = int(abs(rng.normal(7000, 3000)) * (np.exp(-25 * atm_dist**2) + 0.05) * 1.3)
            records_vv.append({"strike": float(k), "oi_call": oi_c, "oi_put": oi_p,
                               "iv_c": round(iv_c, 4), "iv_p": round(iv_p, 4),
                               "gamma_c": round(gamma, 6), "gamma_p": round(gamma, 6),
                               "delta_call": round(dc, 4), "delta_put": round(dc - 1.0, 4),
                               "vanna_c": round(gamma * (1-dc) / max(iv_c, 0.01) * atm_dist, 4),
                               "vanna_p": round(gamma * (1-abs(dc-1)) / max(iv_p, 0.01) * atm_dist, 4)})
        spot_vv = spot_vv  # Use the synthetic spot

    gex_vv = compute_gex_plus(records_vv, spot_vv)
    vex_vv = compute_vanna_exposure(records_vv, spot_vv)
    vgr_vv = abs(vex_vv) / abs(gex_vv) if abs(gex_vv) > 1e-6 else 0.0
    zg_vv = find_zero_gamma(records_vv, spot_vv)
    max_pain_vv, _ = compute_max_pain(records_vv, spot_vv)
    gamma_walls_vv = compute_gamma_walls(records_vv, spot_vv)
    dn_vv = compute_delta_neutral_strike(records_vv, spot_vv)

    mcols_gex = st.columns(6)
    with mcols_gex[0]: st.metric("Net GEX+", f"${gex_vv/1e9:.2f}B", "Long Gamma" if gex_vv > 0 else "Short Gamma")
    with mcols_gex[1]: st.metric("VEX", f"${vex_vv/1e6:.1f}M")
    with mcols_gex[2]: st.metric("VGR", f"{vgr_vv:.3f}", "Vanna Dominant" if vgr_vv > 1 else "Gamma Dominant")
    with mcols_gex[3]: st.metric("Zero Gamma", f"{zg_vv:,.0f}", f"{(zg_vv/spot_vv-1)*100:+.1f}%")
    with mcols_gex[4]: st.metric("Max Pain", f"{max_pain_vv:,.0f}" if max_pain_vv else "N/A", f"{(max_pain_vv/spot_vv-1)*100:+.1f}%" if max_pain_vv else None)
    with mcols_gex[5]: st.metric("Delta Neutral", f"{dn_vv:,.0f}" if dn_vv else "N/A", f"{(dn_vv/spot_vv-1)*100:+.1f}%" if dn_vv else None)

    if gamma_walls_vv:
        wall_str = " | ".join([f"{w['strike']:,.0f} (${w['gex']/1e9:.1f}B)" for w in gamma_walls_vv[:4]])
        st.caption(f"Top Gamma Walls: {wall_str}")

    c1, c2 = st.columns(2)
    with c1:
        st.pyplot(chart_gex_vex_vgr(records_vv, spot_vv))
    with c2:
        profile = compute_crash_profile(records_vv, spot_vv)
        st.pyplot(chart_gex_profile(profile, spot_vv, zg_vv))

# ==============================================================================
# TAB 4: MARKETGUARDIAN PRO
# ==============================================================================
with tabs[3]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>MARKETGUARDIAN PRO -- Market Stress Early Warning System</span>", unsafe_allow_html=True)
    try:
        html_path = pathlib.Path(__file__).parent / "marketguardian_pro.html"
        if html_path.exists():
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            vd_mg = fetch_yahoo()
            vs_mg = _s(vd_mg.get("VIX"))
            vvs_mg = _s(vd_mg.get("VVIX"))
            vix_val_mg = f"{vs_mg.iloc[-1]:.1f}" if vs_mg is not None and len(vs_mg)>0 else "N/A"
            vvix_val_mg = f"{vvs_mg.iloc[-1]:.1f}" if vvs_mg is not None and len(vvs_mg)>0 else "N/A"
            now_str = datetime.utcnow().strftime("%d.%m.%Y, %H:%M:%S")
            html_content = html_content.replace(
                'Letzte Aktualisierung: <span id="last-update">25.10.2025, 21:42:24</span>',
                f'Letzte Aktualisierung: <span id="last-update">{now_str}</span>')
            for old in ['>18.5 <span class="status-indicator status-green"></span>']:
                html_content = html_content.replace(old, f'>{vix_val_mg} <span class="status-indicator status-green"></span>', 1)
            for old in ['>95.3 <span class="status-indicator status-orange"></span>']:
                html_content = html_content.replace(old, f'>{vvix_val_mg} <span class="status-indicator status-orange"></span>', 1)
            st.components.v1.html(html_content, height=900, scrolling=True)
        else:
            st.error("marketguardian_pro.html not found")
    except Exception as e:
        st.error(f"MarketGuardian Pro error: {e}")

# ==============================================================================
# TAB 5: CRYPTO ULTRA
# ==============================================================================
with tabs[4]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>CRYPTO ULTRA -- Deribit + CoinGecko</span>", unsafe_allow_html=True)
    with st.spinner("Loading crypto data..."):
        deribit = fetch_deribit()
        cg = fetch_coingecko()
        fg_c = fetch_fear_greed()

    if fg_c:
        fg_val_c = fg_c.get("value",0)
        fg_label_c = fg_c.get("label","Unknown")
        fg_color_c = "#00ff88" if fg_val_c>60 else "#ffaa00" if fg_val_c>40 else "#ff6600" if fg_val_c>20 else "#ff4444"
        st.markdown(f"""<div style="background:#1a2332;border:1px solid {fg_color_c};border-radius:4px;padding:10px 16px;margin:8px 0">
<span style="color:{fg_color_c};font-size:1.1rem;font-weight:700;font-family:monospace">{fg_val_c}</span>
<span style="color:{fg_color_c};font-size:0.8rem;margin-left:8px;font-family:sans-serif">{fg_label_c}</span>
</div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>PERPETUAL FUTURES (DERIBIT)</span>", unsafe_allow_html=True)
    pc = st.columns(4)
    for idx, sym in enumerate(["btc","eth"]):
        if sym in deribit["perpetuals"]:
            p = deribit["perpetuals"][sym]
            with pc[idx*2]:
                st.metric(f"{sym.upper()}-PERP", fmt_price(p.get("last")), f"{p.get('change_pct',0):+.2f}%")
            with pc[idx*2+1]:
                st.markdown(f"<span style='color:#8899aa;font-size:0.75rem;font-family:monospace'>Mark: {fmt_price(p.get('mark'))} | OI: {p.get('oi',0):,.0f}</span>", unsafe_allow_html=True)
                st.markdown(f"<span style='color:#8899aa;font-size:0.75rem;font-family:monospace'>Funding: {p.get('funding_8h',0):.8f}</span>", unsafe_allow_html=True)

    for sym in ["btc","eth"]:
        if sym in deribit.get("orderbooks",{}):
            st.markdown(f"<span style='color:#8899aa;font-size:0.75rem;font-family:sans-serif'>{sym.upper()} Order Book</span>", unsafe_allow_html=True)
            st.pyplot(chart_order_book(deribit["orderbooks"][sym], sym))

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>COINGECKO TOP 20</span>", unsafe_allow_html=True)
    if cg:
        cg_data = []
        for sym_c, c in list(cg.items())[:20]:
            cg_data.append({
                "Rank":c.get("rank",""),"Symbol":sym_c,"Name":c.get("name",""),
                "Price":fmt_price(c.get("price")),"1h":fmt_pct(c.get("chg_1h")),
                "24h":fmt_pct(c.get("chg_24h")),"7d":fmt_pct(c.get("chg_7d")),
                "Mkt Cap":f"${c.get('mkt_cap',0)/1e9:.1f}B" if c.get('mkt_cap') else "N/A"
            })
        st.dataframe(pd.DataFrame(cg_data), use_container_width=True, hide_index=True, height=400)

    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>7-DAY SPARKLINES</span>", unsafe_allow_html=True)
    spark_cols = st.columns(5)
    for i, sym_s in enumerate(["BTC","ETH","SOL","BNB","XRP"]):
        if sym_s in cg and cg[sym_s].get("sparkline"):
            with spark_cols[i]:
                prices_sp = cg[sym_s]["sparkline"]
                if prices_sp:
                    st.markdown(f"<span style='color:#8899aa;font-size:0.7rem;font-family:sans-serif'>{sym_s} -- {fmt_price(cg[sym_s]['price'])}</span>", unsafe_allow_html=True)
                    st.pyplot(chart_sparkline(prices_sp, sym_s, cg[sym_s]["price"]))

# ==============================================================================
# TAB 6: INSIDER TRADES
# ==============================================================================
with tabs[5]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>CONGRESSIONAL INSIDER TRADES -- CongressInvests</span>", unsafe_allow_html=True)
    with st.spinner("Loading..."):
        congress = fetch_congress()
    h = congress.get("health",{})
    if h:
        st.caption(f"API: {h.get('status','?')} | Tickers: {h.get('tickers',0)} | Updated: {h.get('last_updated','?')}")
    trades = congress.get("trades",[])
    if trades:
        st.markdown(f"<span style='color:#c8d6e5;font-family:monospace;font-size:0.85rem'>{len(trades)} recent trades</span>", unsafe_allow_html=True)
        sc = st.columns(4)
        with sc[0]: st.metric("Total", len(trades))
        with sc[1]: st.metric("Buyers", sum(1 for t in trades if t.get("transaction_type","").lower() in ["buy","purchase"]))
        with sc[2]: st.metric("Sellers", sum(1 for t in trades if t.get("transaction_type","").lower()=="sell"))
        with sc[3]: st.metric("Unique Tickers", len(set(t.get("ticker","") for t in trades if t.get("ticker"))))
        td = [{"Date":t.get("transaction_date",""),"Politician":t.get("member",""),"Party":t.get("party",""),"Chamber":t.get("chamber",""),"Ticker":t.get("ticker",""),"Type":t.get("transaction_type",""),"Amount":t.get("amount","")} for t in trades]
        st.dataframe(pd.DataFrame(td), use_container_width=True, height=500)
    else:
        st.info("CongressInvests data unavailable")

# ==============================================================================
# TAB 7: SETTINGS
# ==============================================================================
with tabs[6]:
    st.markdown("<span style='color:#00d4ff;font-size:0.85rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>SETTINGS &amp; API REFERENCE</span>", unsafe_allow_html=True)
    demo_str = '1' if DEMO_MODE else '0'
    fred_str = '***' if os.environ.get('FRED_API_KEY') else 'NOT SET'
    st.code(f"DEMO_MODE={demo_str}\nFRED_API_KEY={fred_str}")
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>API KEY REFERENCE</span>", unsafe_allow_html=True)
    st.markdown("| Key | Provider | Required |\n|-----|----------|----------|\n| `FRED_API_KEY` | FRED (Fed) | Optional |\n| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage | Optional |\n| `COINGECKO_API_KEY` | CoinGecko | Optional |\n| `DERIBIT_API_KEY` | Deribit | Not needed |\n| `BINGX_API_KEY` | BingX | Not needed |")
    st.markdown("<div style='height:1px;background:#2a3a5a;margin:12px 0'></div>", unsafe_allow_html=True)
    st.markdown("<span style='color:#00d4ff;font-size:0.8rem;font-weight:700;letter-spacing:0.04em;font-family:sans-serif'>ASSET VOLATILITY MAPPING</span>", unsafe_allow_html=True)
    map_rows = []
    for asset, info in ASSET_VOLA_MAP.items():
        map_rows.append({"Asset": asset, "Name": info["name"], "Vola Index": info["vola"]})
    st.dataframe(pd.DataFrame(map_rows), use_container_width=True, hide_index=True)

st.markdown("<div style='height:1px;background:#2a3a5a;margin:16px 0 8px 0'></div>", unsafe_allow_html=True)
st.markdown("<span style='color:#3a4a5a;font-size:0.65rem;font-family:monospace'>KRUPP CAPITAL | Quantitative Desk | Precision in Chaos, Alpha in Variance</span>", unsafe_allow_html=True)
