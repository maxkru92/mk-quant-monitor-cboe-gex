# 🤖 @GEX_Worker_KC_bot — Bot Description (v4.0)

> **@GEX_Worker_KC_bot** — Live GEX Levels für SPX, NDX, RUT, VIX, SPY, QQQ & IWM alle 15 Minuten. Zwei Formate: Standard (mehrzeilig) oder MenthorQ (einzeilig CSV). 0DTE-Levels + 1D Min/Max. Daten: CBOE Delayed Quotes. Befehle: /start /status /format standard|menthorq /help.

---

## Vollständige Beschreibung

**@GEX_Worker_KC_bot — GEX Levels & Gamma Intelligence**

Live Options-Gamma-Exposure (GEX) für SPX, NDX, RUT, VIX, SPY, QQQ & IWM — alle 15 Minuten direkt in Telegram. **Open Bot** — jeder kann ihn anschreiben und wird automatisch für den 15-Minuten-Broadcast registriert.

---

## ✅ Hauptfunktionen (v4.0)

| Feature | Beschreibung |
|---|---|
| **⏱️ 15-Minuten-GEX-Report** | Automatischer Cron-Job sendet aktuelle GEX-Levels alle 15 Minuten. |
| **📊 Multi-Symbol** | Unterstützt SPX, NDX, RUT, VIX, SPY, QQQ, IWM — einzeln oder kombiniert. |
| **📑 Zwei Formate pro Nutzer** | Standard (mehrzeilig Markdown) **oder** MenthorQ (einzeilig CSV) — umschaltbar via `/format`. |
| **🔥 0DTE separat** | Same-Day-Expiry-Block mit Call Resistance, Put Support, HVL & Gamma Wall. |
| **📈 1D Min / 1D Max** | Intraday High/Low aus Yahoo Finance (für MenthorQ-Format). |
| **🎯 Top-10 GEX-Strikes** | Top-10-Strikes nach absolutem Net-GEX — als „GEX 1 … GEX 10" in der MenthorQ-Zeile. |
| **🚨 Regime-Change-Alerts** | Benachrichtigung, wenn sich das Gamma-Regime verschiebt. |
| **📈 Spot + IV30** | Aktueller Spot-Preis, IV30 und Tagesveränderung aus CBOE-Daten. |
| **💾 Automatisches Abo** | Jede eingehende Nachricht abonniert den Chat für den 15-Minuten-Broadcast. |
| **🛠️ Manuelle Trigger** | Admins können Reports manuell über `/broadcast-test` auslösen. |

---

## 📡 Datenquellen

- **Primär:** CBOE Delayed Quotes (kostenlos, stabil, ~15 Min verzögert)
- **Fallback Spot:** Yahoo Finance
- **Fallback Intraday:** Yahoo Finance 5-Minuten-Kerzen (1D Min/Max)
- **Letzte Rettung:** BSM-Synthetic-Chain

---

## 💬 Alle Befehle

| Befehl | Funktion |
|---|---|
| `/start` | Begrüßung, zeigt Chat-ID und aktuelles Format. Auto-Subscribe. |
| `/help` | Hilfe & vollständige Befehlsliste |
| `/status` | Aktuelle GEX-Levels in deinem Format (alle konfigurierten Symbole) |
| `/status SPX` | Nur ein bestimmtes Ticker-Symbol |
| `/format standard` | Wechsel zu mehrzeiligem Markdown-Standardformat |
| `/format menthorq` | Wechsel zu MenthorQ einzeiligem CSV-Format |
| `/symbols` | Liste aller unterstützten Symbole |
| `/subscribe SPX` | Explizit nur für ein Symbol anmelden |
| `/unsubscribe SPX` | Für ein Symbol abmelden |
| Plain Text `SPX` / `$SPX` | Sofortige Quote für das Ticker-Symbol |
| Plain Text andere Nachricht | Freundlicher Hinweis auf `/help` |

---

## 📋 Format-Vergleich

### Standardformat (Default bei `/start`)

```
*SPX* | GEX Report
━━━━━━━━━━━━━━━━━━━━━
💰 Spot: *7515.34* (-0.80%)
📊 IV30: 13.20% | Regime: 🔴 NEGATIVE_GAMMA
📈 Net GEX: *-181.4M*

🔵 Call Wall: *7515* (19.9M)
🔴 Put Support: *7515* (187.3M)
⚖️ HVL: *7515*

📅 Expiry: 2026-07-16 (DTE: 2) | Strikes: 249
📡 Source: cboe
📊 1D Min: 7451.62 | 1D Max: 7580.18

🔥 *0DTE Levels:*
  CR: 7550 | PS: 7475 | HVL: 7530

🔵 Top Call GEX:
  7515: 19.9M (OI: 12,345)
…
━━━━━━━━━━━━━━━━━━━━━
_Krupp Capital Quantitative Desk_
```

### MenthorQ-Format (nach `/format menthorq`)

Einzeiliger CSV-String pro Symbol — kompatibel mit dem MenthorQ „Gamma Levels EOD"-Stil:

```
$SPX: Call Resistance, 7515, Put Support, 7515, HVL, 7515, 1D Min, 7451.62, 1D Max, 7580.18, Call Resistance 0DTE, 7550, Put Support 0DTE, 7475, HVL 0DTE, 7530, Gamma Wall 0DTE, 7550, GEX 1, 7515, GEX 2, 7485, GEX 3, 7525, GEX 4, 7500, GEX 5, 7575, GEX 6, 7455, GEX 7, 7400, GEX 8, 7450, GEX 9, 7620, GEX 10, 7625
```

Wenn heute kein 0DTE verfügbar ist:

```
$SPX: …, Call Resistance 0DTE, N/A, Put Support 0DTE, N/A, HVL 0DTE, N/A, Gamma Wall 0DTE, N/A, …
```

---

## 🎯 Ideale Nutzung

Perfekt für **Optionshändler, Volatility-Trader und Quant-Analysten**, die schnell sehen wollen wo die wichtigsten Gamma-Levels liegen und wie sich das Gamma-Regime entwickelt. Mit dem MenthorQ-Format lässt sich der Output direkt in Excel/Spreadsheets oder Notizen-Tools weiterverarbeiten.

---

## 🔐 Sicherheit

- Optional `WEBHOOK_SECRET_TOKEN` setzen → Telegram verifiziert das Shared Secret bei jedem Webhook-Call
- Webhook-Setup erfolgt manuell via `https://gex-collector.maxkrupp.workers.dev/setup-webhook`

---

## ⚠️ Disclaimer

Dieser Bot dient ausschließlich der **Information und Bildung**. Es handelt sich **nicht um Anlageberatung**. Handelsentscheidungen erfolgen auf eigene Verantwortung.

**Krupp Capital Quantitative Desk** — Precision in Chaos, Alpha in Variance.
