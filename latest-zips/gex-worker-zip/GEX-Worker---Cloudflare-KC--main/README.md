<img width="1024" height="559" alt="GEX Worker Dashboard" src="https://github.com/user-attachments/assets/62e5b70a-a671-44bb-ad08-f248fb1f0000" />

# GEX Worker — Cloudflare Worker (v4.0)

**Gamma Exposure (GEX) computation engine deployed as Cloudflare Worker — with open Telegram bot and MenthorQ-compatible single-line output.**

Dieser Worker holt Optionsketten von der CBOE, berechnet Gamma-Exposure-Levels in Echtzeit und sendet alle 15 Minuten einen strukturierten GEX-Report per Telegram an registrierte Chats. Bei Regime-Changes wird ein zusätzlicher Alert ausgelöst. Ab v4.0 akzeptiert er Nachrichten von **jedem Telegram-Nutzer** und unterstützt **zwei Ausgabeformate** (Standard mehrzeilig oder MenthorQ einzeilig).

---

## 🆕 Was ist neu in v4.0

| Feature | Beschreibung |
|---|---|
| **🤖 Open Telegram-Bot** | Jeder kann den Bot anschreiben — beim ersten Kontakt wird der Chat automatisch für den 15-Minuten-Broadcast registriert. |
| **📑 Zwei Ausgabeformate pro Nutzer** | Standardformat (mehrzeilig, Markdown) **oder** MenthorQ-Style (einzeilig, kommasepariert) — umschaltbar mit `/format standard\|menthorq`. |
| **🔥 0DTE separat** | Same-Day-Expiry (wenn vorhanden) wird als eigener Block mit Call Resistance, Put Support, HVL & Gamma Wall berechnet. |
| **📈 1D Min / 1D Max** | Intraday-High/Low aus Yahoo Finance für das MenthorQ-Format. |
| **🎯 Top-10 GEX-Strikes** | Top-10-Strikes nach absolutem Net-GEX — als GEX 1 … GEX 10 in der MenthorQ-Zeile. |
| **🔐 Optional Webhook-Secret** | Shared-Secret-Token zwischen Telegram und Worker zum Schutz vor unauthentifizierten Webhook-POSTs. |

---

## 🚀 Features (komplett)

| Feature | Beschreibung |
|---|---|
| **⏱️ 15-Minuten-Cron** | Automatische GEX-Berechnung und Telegram-Broadcast alle 15 Minuten |
| **📊 Multi-Symbol** | SPX, NDX, RUT, VIX, SPY, QQQ, IWM konfigurierbar |
| **📡 Primäre Datenquelle CBOE** | Kostenlos, stabil, liefert Spot + Optionsketten + Greeks |
| **🔁 Fallbacks** | Yahoo Finance für Spot-Preise + Intraday Min/Max; BSM-Synthetik als letzte Rettung |
| **🧮 GEX-Berechnung** | `Gamma × OI × Spot² / 100` (institutional standard) |
| **🎯 Key Levels** | Call Wall, Put Support, HVL, Net GEX, Regime |
| **🔥 0DTE / Front-Expiry** | Filter auf nächste Laufzeit, dedupliziert Strikes, 0DTE-Block separat |
| **🚨 Regime-Change-Alerts** | Benachrichtigung bei signifikanten GEX-Regime-Wechseln |
| **💬 Telegram-Bot** | Direktnachrichten an registrierte Chats, Abo-Management |
| **🌐 HTTP-API** | REST-Endpunkte für Latest, Previous, Compare, Trigger, Alerts |
| **📦 Cloudflare KV** | Persistenz für Latest, Previous, Alerts, Subscriptions, User-Formats |

---

## 🏗️ Architektur

```
Cron (every 15 min)               Telegram Webhook (any user)
    │                                      │
    ▼                                      ▼
┌─────────────────────────────────────────────────────────────┐
│  GEX Worker (Cloudflare)                                    │
│                                                             │
│  1. fetchCBOESpot()      → CBOE Info                        │
│  2. fetchCBOEChain()     → CBOE Chain (incl. 0DTE separate) │
│  3. fetchIntradayRange() → Yahoo 1D Min/Max                 │
│  4. computeGEX()         → GEX Levels (+ Top-10 Net GEX)    │
│  5. KV write             → GEX_KV                            │
│  6. broadcastGexReport() → Telegram (per-user format)       │
│  7. broadcastRegimeChange() (if Δ)                          │
└─────────────────────────────────────────────────────────────┘
    │                                      │
    ▼                                      ▼
┌──────────────────────┐         ┌─────────────────────────────┐
│  Cloudflare KV       │         │  Telegram Bot API           │
│  gex:SPX:latest      │         │  - sendMessage (rate-limited│
│  gex:SPX:previous    │         │    25 msg/sec, chunked)     │
│  gex:subs:SPX        │         └─────────────────────────────┘
│  gex:user:<chatId>   │
│  gex:alerts          │
│  gex:push-queue      │
└──────────────────────┘
```

---

## 📡 API Endpoints

| Method | Endpoint | Beschreibung |
|---|---|---|
| GET | `/health` | Worker-Status + Version |
| GET | `/status` | Übersicht aller Symbole + letzte Daten |
| GET | `/latest?symbol=SPX` | Aktuellste GEX-Daten für ein Symbol |
| GET | `/previous?symbol=SPX` | Vorheriger Lauf |
| GET | `/compare?symbol=SPX` | Regime-Change-Vergleich |
| GET | `/symbols` | Übersicht aller konfigurierten Symbole |
| GET | `/alerts` | Letzte 20 Regime-Alerts |
| GET/POST | `/trigger?symbol=SPX` | Manuelle Berechnung |
| POST | `/webhook` | TradingView / externer Webhook |
| POST | `/subscribe?symbol=SPX&chat_id=...` | Telegram-Abo hinzufügen |
| POST | `/unsubscribe?symbol=SPX&chat_id=...` | Telegram-Abo entfernen |
| GET | `/subscriptions?symbol=SPX` | Aktive Subscriptions anzeigen |
| POST | `/telegram-webhook` | Telegram ruft diesen Endpunkt auf |
| GET | `/setup-webhook?secret=…` | Registriert die Webhook-URL bei Telegram |
| GET | `/webhook-info` | Aktueller Webhook-Status bei Telegram |
| GET | `/clear-webhook` | Webhook bei Telegram löschen |
| GET | `/broadcast-test?symbol=SPX&format=menthorq` | Test-Broadcast (admin) |

### Beispiel-Response `/latest?symbol=SPX`

```json
{
  "timestamp": "2026-07-14T06:30:18.000Z",
  "symbol": "SPX",
  "spot": 7515.34,
  "spotSource": "cboe",
  "iv30": 13.20,
  "regime": "NEGATIVE_GAMMA",
  "netGex": -181400000,
  "netGexFormatted": "-181.4M",
  "callWall": { "strike": 7515, "gex": "19.9M" },
  "putSupport": { "strike": 7515, "gex": "187.3M" },
  "hvl": 7515,
  "dayMin": 7451.62,
  "dayMax": 7580.18,
  "topNetGexStrikes": [7515, 7485, 7525, 7500, 7575, 7455, 7400, 7450, 7620, 7625],
  "zeroDte": {
    "expiry": "2026-07-14",
    "dte": 0,
    "callResistance": 7550,
    "putSupport": 7475,
    "hvl": 7530,
    "gammaWall": 7550,
    "netGex": 25000000
  },
  "chainSource": "cboe",
  "strikeCount": 249,
  "frontExpiry": "2026-07-16",
  "dte": 2
}
```

---

## 🧮 GEX-Formel

**Standard (institutional):**

```
GEX = Gamma × OI × Spot² / 100
```

- **Gamma**: Gamma pro 1 $ Bewegung (von CBOE oder BSM-Approximation)
- **OI**: Open Interest
- **Spot**: Aktueller Underlying-Preis
- **Ergebnis**: Dollar-Exposure pro 1 % Bewegung

### Gamma-Approximation (wenn CBOE Gamma = 0)

```javascript
function bsmGamma(S, K, sigma, T) {
  const d1 = (Math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * Math.sqrt(T));
  const nd1 = Math.exp(-0.5 * d1 * d1) / Math.sqrt(2 * Math.PI);
  return nd1 / (S * sigma * Math.sqrt(T));
}
```

### Top-10 Net-GEX (MenthorQ-kompatibel)

```javascript
const allNet = strikes.map(s => ({
  strike: s.strike,
  netGex: s.callGamma * s.callOI * spot * spot / 100
        - s.putGamma * s.putOI * spot * spot / 100
}));
allNet.sort((a, b) => Math.abs(b.netGex) - Math.abs(a.netGex));
const top10 = allNet.slice(0, 10).map(s => s.strike);
```

---

## 📊 Datenquellen (Priorität)

1. **CBOE** — Primär für SPX, VIX, NDX, RUT (delayed ~15 Minuten, keine Rate-Limit)
2. **Yahoo Finance** — Fallback für Spot-Preise **+ Intraday 1D Min/Max** (5-Minuten-Kerzen)
3. **BSM Synthetic** — Letzte Rettung bei komplettem Chain-Ausfall

---

## 🤖 Telegram-Bot

### Bot-Name

**@GEX_Worker_KC_bot**

### Was der Bot sendet

- **Alle 15 Minuten**: GEX-Report — im **Standardformat** (mehrzeilig) oder im **MenthorQ-Format** (einzeilig), je nach Nutzer-Präferenz
- **Bei Regime-Change**: Sofort-Alert mit neuem Regime, Net GEX, Call Wall, Put Support
- **Bei direktem Anschreiben**: Sofortige Antwort mit aktuellem Level im bevorzugten Format

### Befehle

| Befehl | Wirkung |
|---|---|
| `/start` | Begrüßung, zeigt deine Chat-ID und das aktuelle Format. Auto-Subscribe. |
| `/help` | Hilfe & vollständige Befehlsliste |
| `/status` | Aktuelle GEX-Levels in deinem Format (Standard oder MenthorQ) |
| `/status SPX` | Nur SPX (oder ein anderes unterstütztes Symbol) |
| `/format standard` | Wechsel zu mehrzeiligem Markdown-Standardformat |
| `/format menthorq` | Wechsel zu MenthorQ einzeiligem CSV-Format |
| `/symbols` | Liste aller unterstützten Symbole |
| `/subscribe SPX` | Nur explizit für ein Symbol anmelden |
| `/unsubscribe SPX` | Für ein Symbol abmelden |
| Plain Text `SPX` / `$SPX` | Sofortige Quote für das Ticker-Symbol |
| Plain Text andere Nachricht | Freundlicher Hinweis auf `/help` |

### Auto-Subscribe

**Jede eingehende Nachricht** registriert den Chat automatisch im 15-Minuten-Broadcast. Keine manuelle `/subscribe`-Aktion nötig — einfach dem Bot schreiben und loslegen.

### Format-Vergleich

#### Standardformat (default)

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

#### MenthorQ-Format (`/format menthorq`)

Einzeiliger CSV-String pro Symbol, kompatibel mit dem MenthorQ „Gamma Levels EOD"-Stil:

```
$SPX: Call Resistance, 7515, Put Support, 7515, HVL, 7515, 1D Min, 7451.62, 1D Max, 7580.18, Call Resistance 0DTE, 7550, Put Support 0DTE, 7475, HVL 0DTE, 7530, Gamma Wall 0DTE, 7550, GEX 1, 7515, GEX 2, 7485, GEX 3, 7525, GEX 4, 7500, GEX 5, 7575, GEX 6, 7455, GEX 7, 7400, GEX 8, 7450, GEX 9, 7620, GEX 10, 7625
```

Falls heute kein 0DTE verfügbar ist:

```
$SPX: …, Call Resistance 0DTE, N/A, Put Support 0DTE, N/A, HVL 0DTE, N/A, Gamma Wall 0DTE, N/A, …
```

---

## 🔌 Telegram-Webhook einrichten

### 1. Cloudflare-Login

```bash
npx wrangler login
```

### 2. Secrets setzen

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
# Optional: Shared Secret für Webhook-Authentifizierung
npx wrangler secret put WEBHOOK_SECRET_TOKEN
```

### 3. Deploy

```bash
npx wrangler deploy
```

### 4. Webhook bei Telegram registrieren

Nach dem Deploy ruft du einmalig `/setup-webhook` auf. Das assoziiert die Worker-URL mit deinem Bot.

```bash
# mit Shared Secret (empfohlen)
curl "https://gex-collector.YOUR_SUBDOMAIN.workers.dev/setup-webhook?secret=DEIN_GEHEIMNIS"

# ohne Secret (Webhook ist offen)
curl "https://gex-collector.YOUR_SUBDOMAIN.workers.dev/setup-webhook"
```

Antwort:

```json
{ "ok": true, "result": true, "description": "Webhook was set" }
```

### 5. Webhook-Status prüfen

```bash
curl https://gex-collector.YOUR_SUBDOMAIN.workers.dev/webhook-info
```

### 6. Webhook zurücksetzen

```bash
curl https://gex-collector.YOUR_SUBDOMAIN.workers.dev/clear-webhook
```

---

## 🛠️ Lokale Entwicklung

### Voraussetzungen

- Node.js ≥ 18
- Cloudflare-Konto + Wrangler-Login

### Installation

```bash
git clone https://github.com/maxkru92/GEX-Worker---Cloudflare-KC-.git
cd GEX-Worker---Cloudflare-KC-
npm install
```

### Lokaler Test

```bash
npx wrangler dev
```

### Tests

```bash
node test-gex.mjs
```

---

## 🚀 Deployment (Zusammenfassung)

```bash
# 1. Login
npx wrangler login

# 2. Secrets
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
npx wrangler secret put WEBHOOK_SECRET_TOKEN    # optional

# 3. Deploy
npx wrangler deploy

# 4. Webhook registrieren
curl "https://gex-collector.YOUR_SUBDOMAIN.workers.dev/setup-webhook?secret=DEIN_SECRET"

# 5. Testen: dem Bot eine Nachricht schicken
```

---

## ⚙️ Konfiguration

### `wrangler.toml`

```toml
name = "gex-collector"
main = "src/index.js"
compatibility_date = "2024-09-23"
compatibility_flags = ["nodejs_compat"]

[triggers]
crons = ["*/15 * * * *"]

[[kv_namespaces]]
binding = "GEX_KV"
id = "bb9f6786bd5242bc8c89ac3c676916f3"

[vars]
SYMBOLS = "SPX,VIX"
LOG_LEVEL = "info"
HF_WEBHOOK_URL = "https://maxkru92-hermes-neu-volatility-vince.hf.space/webhook"
```

### Geheime Schlüssel & Variablen

| Variable | Beschreibung | Erforderlich |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot-Token von @BotFather | Ja |
| `TELEGRAM_CHAT_ID` | Standard-Chat-ID für 15-Minuten-Reports | Empfohlen |
| `WEBHOOK_SECRET_TOKEN` | Shared Secret für Telegram-Webhook (setze via `setWebhook?secret_token=…`) | Optional |
| `SYMBOLS` | Komma-separierte Symbole (env `vars`) | Default: SPX,VIX |
| `HF_WEBHOOK_URL` | Optionaler externer Webhook | Nein |

### Cloudflare-KV-Layout

| Key | Inhalt |
|---|---|
| `gex:<SYM>:latest` | JSON: aktuelle GEX-Daten inkl. dayMin, dayMax, zeroDte, topNetGexStrikes |
| `gex:<SYM>:previous` | JSON: voriger Lauf (für Regime-Vergleich) |
| `gex:alerts` | JSON-Array: letzte 20 Regime-Change-Alerts |
| `gex:push-queue` | JSON-Array: Broadcast-Historie |
| `gex:sub:<SYM>:<chatId>` | JSON: einzelne Subscription-Metadaten |
| `gex:subs:<SYM>` | JSON-Array: alle Chat-IDs für ein Symbol |
| `gex:user:<chatId>` | JSON: `{ format: "standard"\|"menthorq" }` |

---

## 📁 Projektstruktur

```
gex-worker/
├── src/
│   └── index.js              # Haupt-Worker (Cron + HTTP + Telegram-Webhook)
├── pinescript/
│   └── GEX_Levels_Regime.pine  # TradingView PineScript Indikator
├── test-gex.mjs              # Unit-Tests
├── package.json              # Node-Projekt + Wrangler
├── wrangler.toml             # Cloudflare-Konfiguration
├── README.md                 # Diese Datei
└── BOT_DESCRIPTION.md        # Telegram-Bot-Beschreibung
```

---

## 🔧 Fehlerbehebung

| Problem | Lösung |
|---|---|
| `TELEGRAM_BOT_TOKEN not set` | Secret via `wrangler secret put` setzen |
| `no chat_id, skipping` | `TELEGRAM_CHAT_ID` fehlt oder Auto-Subscribe hat nicht gefeuert |
| CBOE-Chain leer | CBOE-Daten ~15 Min verzögert; Yahoo/BSM-Fallback greift |
| Regime-Alert kommt nicht | Threshold 15 % oder 100 Mio. USD Minimum |
| Webhook gibt 200, aber keine Subscriptions | lv4.0: ein Sync-Handler garantiert KV-Writes — siehe Code-Notes |
| MenthorQ-Felder fehlen | Yahoo Intraday oder 0DTE heute nicht verfügbar — `N/A` ist normal |
| `WEBHOOK_SECRET_TOKEN unset — webhook is OPEN` | Shared Secret über `wrangler secret put` setzen, dann `/setup-webhook?secret=…` neu aufrufen |

---

## ⚠️ Sicherheits-Notes

- Ohne `WEBHOOK_SECRET_TOKEN` akzeptiert der Worker **alle** POSTs auf `/telegram-webhook` (offen). Für Produktion **immer** ein Shared Secret setzen.
- Das Secret wird per `setWebhook?secret_token=…` an Telegram übergeben; Telegram sendet dann `X-Telegram-Bot-Api-Secret-Token` bei jedem Webhook-Call. Der Worker verifiziert diesen Header.
- `drop_pending_updates: true` beim Setup verhindert, dass alte Nachrichten vor dem Setup erneut zugestellt werden.

---

## ⚠️ Disclaimer

Dieses Projekt dient ausschließlich der **Information und Bildung**. Es ist **keine Anlageberatung**. Handelsentscheidungen auf eigene Verantwortung.

**Krupp Capital Quantitative Desk** — Precision in Chaos, Alpha in Variance.
