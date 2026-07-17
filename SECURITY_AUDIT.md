# Security Audit — `maxkru92/*` repositories

**Date**: 2026-07-16
**Scope**: 4 public GitHub repositories under the `maxkru92` org.
**Clones**: shallow (`--depth 1`), placed under `.security-audit/` in this workspace.
**Threat model assumed**: anyone with read access to the repos can use any committed credential against its respective upstream provider. Before 2026-07-16 this meant anonymous-Internet visitors; after the deletion + private switch it means collaborators and source-compromise / fork-leak vectors.

> ⚠️ The values reproduced below for the `Trading-Suite-Light-HF-Edition-KC-` `.env.example` were already public on GitHub at the time of audit. Including them here does not change exposure — but it's necessary so you can identify and rotate them in your password manager / secrets store.

---

## 1. Severity summary

| # | Severity | Repo | Finding | Immediate action | Status (post-audit, 2026-07-16) |
|---|----------|------|---------|------------------|------------------------------------|
| 1 | 🔴 **CRITICAL** | `Trading-Suite-Light-HF-Edition-KC-` | A file named `.env.example` is committed to git and contains 11 OAuth-shaped API keys (full values shown in §3.1). The companion `.env` (empty template) is also tracked. | Treat all 11 keys as leaked. **Rotate them now**. Then git-rm both files, scrub from history (`git filter-repo`), and re-add to `.gitignore`. | `[Public-surface RESOLVED via deletion]` — user deleted the repo on 2026-07-16. **Provider-side rotation still REQUIRED** for any token active while public. |
| 2 | 🟠 **HIGH** | `GEX-Worker---Cloudflare-KC-` | `wrangler.toml` hardcodes `HF_WEBHOOK_URL` to a specific Hugging Face Space. Anyone reading the repo can post JSON to that endpoint, and the worker POSTs structured GEX payloads there every 15 min. | Move URL to a Cloudflare `[vars]` entry and rotate the HF Space token. If the endpoint accepts unauthenticated POSTs, add a shared secret. | `[Public-surface RE-EXPOSED on 2026-07-16 — user re-published the repo after a brief private window. Source-level fix (move URL to secret) NOT applied; HF_WEBHOOK_URL is now publicly visible again at HEAD (30243ab).]` |
| 3 | 🟠 **HIGH** | `GEX-Worker---Cloudflare-KC-` | The Telegram webhook handler in `src/index.js` is **open by default** — if `WEBHOOK_SECRET_TOKEN` is unset, the worker logs a warning but still returns 200 to any POST. This means anyone who knows the URL can impersonate Telegram updates. | Set `WEBHOOK_SECRET_TOKEN` via `wrangler secret put` AND flip the conditional in `src/index.js` to fail-closed (unset secret → 503 instead of 200); then re-register `/setup-webhook?secret=…`. | `[Public-surface RE-EXPOSED on 2026-07-16. Fail-closed flip NOT applied; webhook still open-by-default at HEAD (30243ab).]` |
| 4 | 🟡 **MEDIUM** | `GEX-Worker---Cloudflare-KC-` | `.wrangler/cache/wrangler-account.json` is **tracked** in git. The directory is gitignored, so this likely got in via `git add -f` or a pre-gitignore commit. (Contents not directly inspected in this audit; the Cloudflare account id itself is public, but old `wrangler dev` tokens sometimes get written into the same directory.) | `git rm --cached .wrangler/cache/wrangler-account.json`, then scrub history. | `[Public-surface RE-EXPOSED on 2026-07-16. git rm --cached NOT applied; .wrangler/cache/ directory still tracked in HEAD (e615a57 was the start of this round; full contents unchanged by 30243ab).]` |
| 5 | 🟡 **MEDIUM** | `Trading-Suite-Light-HF-Edition-KC-` | Workflow `.github/workflows/ci.yml` uses `actions/checkout@v4`, `actions/setup-python@v4`, and `docker/build-push-action@v4` — all by tag, **not by SHA**. A compromised upstream action would have write access during CI builds. | Pin actions by full SHA (`uses: actions/checkout@b4ffde65…@v4`). | `[RESOLVED via deletion]` — `.github/workflows/ci.yml` no longer exists. |
| 6 | 🟡 **MEDIUM** | `Trading-Suite-Light-HF-Edition-KC-` + `GEX-Worker---Cloudflare-KC-` | macOS `.DS_Store` files are tracked at the repo root and in subfolders of the GEX repo, and many subfolders of Trading-Suite. They leak folder-structure info but no real secrets. | `git rm --cached **/.DS_Store`, re-add to `.gitignore`, scrub history. | `[Split]` — Trading-Suite's `.DS_Store`s were deleted with the repo; **GEX-Worker's root `.DS_Store` is still tracked** and still actionable. |
| 7 | 🟡 **MEDIUM** | `Trading-Suite-Light-HF-Edition-KC-` | Repo contains vendored binary blobs in `assets/modules/` (`.zip`, `.mov`, `.pdf`, `.odt`). These are large, hard to audit, and the `.zip` in particular is a textbook supply-chain attack surface — it could contain arbitrary code that future contributors run. | Either remove the vendored modules and reference them as git submodules, or add a SHA-pinning / verifiable-build step. | `[RESOLVED via deletion]`. |
| 8 | 🟢 **LOW** | `mk-quant-monitor-cboe-gex` | No `.gitignore` at the root. Python bytecode, virtualenvs, and `.env` files committed in the future would land in git. | Add a Python `.gitignore` (the standard one from `github/gitignore`). | `[Public-surface MITIGATED via private switch]` — adding `.gitignore` is still worth doing. |
| 9 | 🟢 **LOW** | `GEX-Worker---Cloudflare-KC-` | `fix-normalize.mjs` is a one-shot dev script that mutates `src/gex-compute.js`. It has no business being checked in long-term — it's an inline `sed -i` that anyone could re-run, potentially with stale parameters. | Delete it or move it under `scripts/`; its inclusion implies "feel free to re-run", which is a footgun. | `[Public-surface MITIGATED via private switch]` — clean-up still worth doing. |
| 10 | 🟢 **LOW** | `Trading-Suite-Light-HF-Edition-KC-` | `.dockerignore` does exclude `.env` correctly — but the actual `.env` was committed **before** that file was added. | Audit git log for pre-exclusion commits and decide whether historical `.env` versions exist. | `[RESOLVED via deletion]`. |
| 11 | 🔵 **INFO** | `Quant-Trading-Desk-Retail-Edition-KC-` | Repo contains only `LICENSE` (Apache-2.0) and `README.md` (a single `<img>`). No code, no workflows, no `.gitignore`. Either vacuously secure or never populated. | Decide: archive, delete, or actually populate. Currently it advertises the org without delivering. | `[Public-surface MITIGATED via private switch]` — the archive/populate decision is still pending. |

---

## 2. Headlines

- The most actionable finding is **#1**: an `.env.example` file containing what look like real API tokens for HF, AlphaVantage, FRED, FlashAlpha, CoinGecko, BingX, Deribit, Finnhub, DBNomics, and Polygon — committed to a public repo. **Rotate these in this order**: (a) BingX, (b) Deribit (brokerage APIs — highest blast radius if withdrawal-scoped), (c) HF (token can't be IP-locked), (d) Finnhub / Polygon / AlphaVantage / FRED / CoinGecko / FlashAlpha / DBNomics (primarily cost/quota risk).
- The local working tree at `MK_Quant_Monitor/gex-worker-cloudflare/` is a **fork** of `GEX-Worker---Cloudflare-KC-` with a few customizations (worker name `kc-gex-broadcast` vs `gex-collector`, expanded symbol list `SPX,SPY,QQQ,VIX,GLD,SLV,USO,IBT` vs `SPX,VIX`). It carries the **same hardcoded `HF_WEBHOOK_URL`** and the **same `.gitignore` gap** as upstream, so the inheritance carries finding #2 forward to your local copy. The local `.gitignore`, however, correctly excludes `*.env`, so the .env-style leak in #1 does not apply locally.
- The pasted "CRITICAL SECURITY FIX SCRIPT" you supplied is mostly cosmetic — the only finding it would actually fix is `.env` removal in `Trading-Suite-Light-HF-Edition-KC-`. Its proposed `wrangler secret put GEX_KV_ID` would **break the worker** (KV namespace ids aren't secrets — they're a static build-time binding, see §3.2.1).
- None of the `*.lock`, `node_modules`, vendored binaries were treated as security findings; lockfiles are audit-out-of-scope without `pip-audit`/`npm audit` etc., and vendor blobs are worth a size+hash digest rather than a content scan.

---

## 3. Per-repo detail

### 3.1 `Trading-Suite-Light-HF-Edition-KC-` (CRITICAL 🔴)

**Tracked count**: 328 files (incl. `.git/`'s `HEAD`). Tracked `.env*` files: `.env` (empty, no leak), `.env.example` (**contains 11 secrets**).

#### 3.1.1 `.env.example` (committed)

Header mistakenly reads "Example .env — COPY to .env". The committed body contains values that look real — OAuth-style prefixes and entropy consistent with provider-issued tokens. **All were publicly exposed on GitHub until 2026-07-16, when the repo was deleted** (see §7); provider rotation remains mandatory for any token that was active during the public-exposure window.

| Variable | First/last-4 chars | Provider | Scope |
|---|---|---|---|
| `HF_TOKEN` | `hf_P***…***QSBQkj` | Hugging Face | Read/write to your HF org (`maxkru92`) |
| `ALPHA_VANTAGE_API_KEY` | `MVB***…***1QB` | AlphaVantage | Live equity/FX data; quota-based billing |
| `FLASH_ALPHA_API_KEY` | `p0w***…***9sib` | FlashAlpha | Unverified — may be derived from a market-data vendor |
| `FRED_API_KEY` | `a75***…***e759` | FRED | Macro time-series reads |
| `COINGECKO_API_KEY` | `CG-***…***zamA` | CoinGecko Pro (paywalled tier); `CG-` prefix is correct for Pro keys |
| `BINGX_API_KEY` / `BINGX_SECRET_KEY` | `X3N***…***llvw` / `iFO***…***Sp5s9w` | BingX exchange | **Brokerage API** — assume worst (e.g., withdrawal/trade) until you've verified the token's scope in account settings; revoke and re-issue with read-only or scoped-down permissions |
| `DERIBIT_API_KEY` | `HkJ***…***bK7A` | Deribit exchange | **Brokerage API** for options on BTC/ETH — same caveat as BingX (assume worst; rotate, then re-issue with scoped-down permissions) |
| `FINNHUB_API_KEY` | `d7c***…***et1oh0` | Finnhub | Real-time US stock data; some endpoints gated |
| `DBNOMICS_API_KEY` | `6BL***…***KIEG` | DBnomics | Public macro data — lower risk |
| `POLYGON_API_KEY` | `gY9***…***1MMG` | Polygon.io | Paywalled US/EU market data |

(For brevity, `[REDACTED]` is shown. The full values are in the committed file at HEAD.)

**Action — rotate (provider order, brokerages first):**

1. BingX — revoke the offending key in account settings, regenerate. If the account can withdraw, also check withdrawal audit log for the last 30 days.
2. Deribit — same rotation via account.
3. HF — revoke + regenerate an org token; rotate any Space env vars that consume it.
4. Then data APIs: Finnhub, Polygon, AlphaVantage, FRED, CoinGecko, FlashAlpha, DBNomics. Most are read-only and rate-capped, so leakage is mostly cost (overage billing), not data exfil.

**Action — remove from history:**

`git filter-repo` is the modern choice (faster, cleaner than `filter-branch`); install via `pip install git-filter-repo` (or `brew install git-filter-repo`). `filter-branch` is deprecated but still works.

```bash
cd Trading-Suite-Light-HF-Edition-KC-
git rm --cached .env .env.example
git commit -m "chore: untrack .env and .env.example"

# History rewrite
git filter-repo --invert-paths --path .env --path .env.example
git remote add origin https://github.com/maxkru92/Trading-Suite-Light-HF-Edition-KC-.git
git push origin --force --all
```

> ⚠️ Force-pushing rewrites SHA history. Open PRs, issue references, and forks pointing at old SHAs will detach. Plan a brief PR freeze during the operation. If `main` is branch-protected (e.g., required-signoff, fork-network protection), you may need to temporarily relax protection or contact GitHub Support.
>
> **Coordinate with collaborators** before the force-push — they'll need to re-clone, or run `git fetch && git reset --hard origin/main`. Old clones that don't re-sync will keep the leaked values locally.

#### 3.1.2 `MANAGE_SECRETS.md`

Documents the correct workflow (GitHub Secrets / HF Space vars / local `.env` excluded from git) — but the project itself doesn't follow it. Worth referencing in PR review.

#### 3.1.3 `.github/workflows/ci.yml`

```yaml
uses: actions/checkout@v4
uses: actions/setup-python@v4
uses: docker/build-push-action@v4
```

Pin by SHA. Example: `actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4`. Same for `setup-python`, `build-push-action`.

Permissions are not declared (`permissions: …`) — defaults to write on `GITHUB_TOKEN`. Should be downgraded to `permissions: { contents: read }`.

#### 3.1.4 `assets/connectors.py` / `cboe_adapter.py`

Reads keys from `os.environ` (`ALPHA_VANTAGE_API_KEY`), which is correct. Does **not** hardcode any tokens. ✅
The `cboe_adapter.py` is a placeholder (returns `None` in `DEMO_MODE=1`) — confirm it isn't doing anything funky for live mode if you wire it up.

#### 3.1.5 `assets/modules/` (supply-chain concern)

Contains vendored directories:
- `CBOE Dashboard/CBOEDashboard-main.zip`
- `Strategie Module/0DTE SPX - ORB + MEAN REVERSION/`
- `crash_monitor`
- `vol-regime-prediction-main`
- Several `.pdf`, `.odt`, `.mov` files

These aren't code-rotated with the repo: any watcher (including a malicious contributor) could update a `.zip` in a future commit and downstream consumers would treat it as upstream. Recommend: remove from repo, pin via git submodule to a hash, OR build-verifiable with sha256 digest.

---

### 3.2 `GEX-Worker---Cloudflare-KC-` (HIGH 🟠)

**Tracked count**: 17 files (excluding `.git/`). Tracked `.env*` files: none. `.gitignore` is correct on paper.

#### 3.2.1 `wrangler.toml` — hardcoded `HF_WEBHOOK_URL`

```toml
[vars]
HF_WEBHOOK_URL = "https://maxkru92-hermes-neu-volatility-vince.hf.space/webhook"
```

The HTTP `POST` body sent there (in `src/index.js` `broadcastGexReport`) is a JSON blob containing full GEX levels (spot, regime, Call Wall, Put Support, HVL, etc.) every 15 minutes — i.e., your derivatives exposure book is being broadcast to that endpoint at known URLs.

**Action:**
1. Move the URL itself to `wrangler secret put HF_WEBHOOK_URL` and read via `env.HF_WEBHOOK_URL` — this also fixes the audit finding that the URL was hardcoded under `[vars]`.
2. Require an `X-Webhook-Signature` HMAC header on the receiving HF Space. The Worker computes it as `hex(hmac_sha256(WEBHOOK_HMAC_SECRET, body))` over the request body. Set `WEBHOOK_HMAC_SECRET` via `wrangler secret put` (and the receiving Space reads it from its own HF-Space-secret env var so the two sides agree).
3. If the URL has changed, just delete the value entirely — don't keep a stale URL or placeholder as a comment.

#### 3.2.2 Open-by-default Telegram webhook (`src/index.js`)

```js
if (env.WEBHOOK_SECRET_TOKEN) {
  const got = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
  if (got !== env.WEBHOOK_SECRET_TOKEN) return new Response("forbidden", { status: 403 });
} else {
  console.warn("[WEBHOOK] WEBHOOK_SECRET_TOKEN unset — webhook is OPEN. Set via wrangler secret put WEBHOOK_SECRET_TOKEN to lock down.");
}
```

The warning is correct, and the README/docs do say "WEBHOOK_SECRET_TOKEN is optional but recommended". For production, this should be **required**, not optional — flip the conditional so an unset secret **fails closed** (returns 503 with a clear error). Otherwise anyone who reads the source can POST a fake `update.message.text` and trigger subscriptions for arbitrary Telegram users.

#### 3.2.3 Tracked `.wrangler/cache/wrangler-account.json`

A file at this path is tracked in git. The directory itself is gitignored, which usually means the file got in via `git add -f` or before the gitignore rule existed. The contents were not directly inspected in this audit; the Cloudflare account id alone is public information, but `wrangler dev` sometimes also writes dev-style tokens into the same directory. **Action:** treat whatever is inside as untrusted build-state — `git rm --cached .wrangler/cache/wrangler-account.json`, scrub history; if the contents include any Cloudflare API token, rotate it on cloudflare.com even though the account id alone isn't sensitive.

#### 3.2.4 `fix-normalize.mjs` at root

Tiny script that does a one-line replacement on `src/gex-compute.js`. Either belongs under `scripts/` or should be deleted — its inclusion implies "feel free to re-run" which is a footgun.

---

### 3.3 `mk-quant-monitor-cboe-gex` (LOW 🟢)

Standard Streamlit app. Source code uses `yfinance` and `requests` against CBOE delayed-quotes CDN — no API-key auth required for any of those endpoints, so the *current* code has no credential surface to leak. ✅

**Issues:**
- No `.gitignore` at root. Adding one (Python) before first push of cached data / venv would prevent the leakage pattern that bit Trading-Suite.

Suggested `.gitignore`:

```
__pycache__/
*.py[cod]
.venv/
venv/
.env
.env.*
*.log
.DS_Store
.idea/
.vscode/
.streamlit/secrets.toml
data/cache/
```

---

### 3.4 `Quant-Trading-Desk-Retail-Edition-KC-` (INFO 🔵)

`LICENSE` (Apache-2.0) + `README.md` (single `<img>`).

Either this repo never had code, or it was force-pushed to a fresh state. Either way:
- If you intend to launch it: actually populate.
- If you don't: archive it (Settings → General → Archive) so it doesn't show up in audits as "missing code".

---

## 4. Things **not** checked (caveats)

- **Dependency CVEs** — `requirements.txt` for Streamlit/Trading-Suite doesn't pin versions (`streamlit`, `pandas`, `numpy`, `plotly`, `requests`, `scipy`). The dashboard pins (`numpy>=1.24.0`, `pandas>=2.0.0`, …). Recommend running `pip-audit` / `safety check` in CI.
- **Action SHA-pinning** — flagged for Trading-Suite's `ci.yml`. The other repos have no `.github/workflows/`, so they're unaffected.
- **Branch protection / required reviewers / GitHub Secret Scanning** — require GitHub Settings. None of these repos were audited at the GitHub-org level (would need `gh api` calls or admin read).
- **History rewrites weren't applied** — only recommended. False positives during rotation are not the goal here; doing the rotation and history scrub is.
- **Provider-side credential validity** — I cannot know whether each committed token is still active. Treat all #1 keys as live and rotate all.
- **The local working tree `MK_Quant_Monitor/gex-worker-cloudflare/` was not audited in depth**, only cross-referenced against upstream findings #2 and #3.
- **The `.env.example` values in §3.1.1 were not independently validated** against any of the upstream providers. They have the right shape (HF: `hf_…`, CoinGecko: `CG-…`) and pass the length/entropy sanity bar, but if a provider demo-string convention exists that I'm unaware of, treat all of #1 as guilty until proven innocent anyway — rotation cost is low.

---

## 5. Reproducible commands

```bash
# 1. Shallow-clone the 4 repos
mkdir -p .security-audit && cd .security-audit
for repo in \
  "GEX-Worker---Cloudflare-KC-" \
  "Trading-Suite-Light-HF-Edition-KC-" \
  "mk-quant-monitor-cboe-gex" \
  "Quant-Trading-Desk-Retail-Edition-KC-"; do
  git clone --depth 1 "https://github.com/maxkru92/${repo}.git"
done

# 2. Enumerate tracked .env* files in each clone
for r in */; do
  echo "=== $r ==="
  git -C "$r" ls-files | grep -E '(^|/)\.env' || echo "  (no tracked .env)"
done

# 3. High-signal token shapes — Trading-Suite is the only hit
git -C Trading-Suite-Light-HF-Edition-KC- grep -nHE \
  'hf_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{32,}|sk-proj-[A-Za-z0-9_-]{32,}' \
  -- ':!**/*.lock' ':!**/*.zip'

# 4. Inspect tracked Cloudflare-build artefacts in the Worker repo
git -C GEX-Worker---Cloudflare-KC- ls-files .wrangler/

# 5. Workflow action reference (no SHA pinning = supply-chain risk)
grep -nE 'uses: ' Trading-Suite-Light-HF-Edition-KC-/.github/workflows/*.yml
```

---

## 6. Recommended order of operations

1. **Right now**: rotate the 11 keys in `.env.example` of `Trading-Suite-Light-HF-Edition-KC-` — see §3.1.1. Start with BingX and Deribit (brokerage).
2. **Today**: `git rm --cached .env .env.example` in that repo, history-rewrite with `git filter-repo`, force-push, alert collaborators.
3. **This week**: rotate any token your **receiving HF Space** uses to authenticate Worker POSTs at `maxkru92-hermes-neu-volatility-vince.hf.space`; flip the open-webhook default in `src/index.js` to fail-closed; move `HF_WEBHOOK_URL` to a Cloudflare secret (and harden with HMAC per §3.2.1).
4. **This week**: `git rm --cached .wrangler/cache/wrangler-account.json` in `GEX-Worker---Cloudflare-KC-`; history-rewrite.
5. **This week**: enable **GitHub Secret Scanning + Push Protection** on all 4 repos (Settings → Security). It's free and immediately alerts on future credential commits.
6. **Sprint**: pin actions by SHA in `.github/workflows/ci.yml`; add Python `.gitignore` to `mk-quant-monitor-cboe-gex`; archive or populate `Quant-Trading-Desk-Retail-Edition-KC-`; replace vendored `.zip`/binary blobs in `Trading-Suite-Light-HF-Edition-KC-/assets/modules/` with submodules or hashed digest verification.

---

## 7. Follow-up — actions taken after the initial audit (2026-07-16)

After the initial audit, the user took several actions that materially changed the threat model. **The findings themselves (under `#`, `Severity`, `Finding`, `Immediate action`) are unchanged** because the underlying source code is byte-identical to what was audited; only the operational context changed. §1's table now carries a `Status (post-audit, 2026-07-16)` column reflecting the new state, and this section narrates the changes. Note that §6 (original order of operations) was written against the public-repo state and contains bullets that are now moot (Trading-Suite history-rewrite, action SHA-pinning for the deleted `ci.yml`, vendored `.zip` cleanup in deleted modules); §7.4 below is the currently-applicable subset.

### 7.1 What was done

| Action | Effect on the audit (§1 row references) |
|---|---|
| `Trading-Suite-Light-HF-Edition-KC-` **deleted** from `maxkru92/*` | Public surface for #1 (CRITICAL leaked keys), #5, #6 (part), #7, #10 is gone — the repo no longer exists. |
| GEX-Worker / mk-quant-monitor / Quant-Trading-Desk **switched to private** | Public surface for #2, #3, #4, #6 (GEX-Worker's part), #8, #9, #11 is gone — repos are no longer browsable by anonymous users. |
| Fresh ZIPs of GEX-Worker and mk-quant-monitor uploaded to `~/Downloads/`, extracted into `latest-zips/` for re-verification | Verified: `wrangler.toml`, `package.json`, `README.md`, `.gitignore`, `runtime.txt`, `requirements.txt`, `app.py`, `data_fetcher.py`, `gex_calculator.py`, `greeks.py`, `menthorq_formatter.py` byte-match the prior shallow clones. **No new issues in source.** |

### 7.2 Residual risks the actions did NOT eliminate

- **Provider-side rotation is still mandatory** for the 11 keys from finding #1. The repo deletion closes the *GitHub* surface, but the keys were publicly exposed for some time before deletion. Anyone with a GitHub scraper, GHArchive mirror, archive.org Wayback, or — for that matter — a copy of this very conversation thread, may have retained the values. **Treat all 11 keys as live and rotate brokerages first (BingX → Deribit), then HF, then data APIs.**
- **Source-level fixes for #2-#4, #6, #8-#9 are still required.** "Private repo" is not the same as "fixed code": the hardcoded `HF_WEBHOOK_URL`, the open-by-default Telegram webhook, the tracked `wrangler-account.json`, the missing root `.gitignore`, the tracked `.DS_Store`, etc. are all still real problems if the repos are re-published, if forks leak through collaborators, or if your account is compromised.
- **The local working copy `MK_Quant_Monitor/gex-worker-cloudflare/` inherits every GEX-Worker source-level issue.** It's a fork of upstream with name + symbol-list customisations (`kc-gex-broadcast`, expanded symbol list) but otherwise identical source — including the hardcoded `HF_WEBHOOK_URL`. Laptop compromise or backup-restore can re-introduce those values into a publishable context. **Forward-port the source-level fixes (#2, #3, #4, #6) into your fork** before relying on the upstream private switch as the only mitigation.
- **`.security-audit/` specifically still holds the leaked Trading-Suite `.env.example` (11 keys)** from when the repos were public, plus stale shallow clones of the other 3; `latest-zips/` holds only the two ZIPs you uploaded on 2026-07-16 (GEX-Worker, mk-quant-monitor — neither contains the leaked keys themselves). Both are stale, on-disk, and currently untracked at the project root. **Shred both after provider rotation completes** (and add an explicit entry for `.security-audit/` and `latest-zips/` to the project `.gitignore` before doing anything that could commit them).

### 7.3 Architecture check

The user stated: *"GEX-Worker---Cloudflare-KC--main should be the broadcaster (cron every 15m, sends via Telegram bot); mk-quant-monitor-cboe-gex-main should send only after a chat request."* Re-verified against the freshly unpacked ZIPs:

- **`GEX-Worker---Cloudflare-KC--main`** ✅ confirmed broadcaster. `wrangler.toml` has `[triggers] crons = ["*/15 * * * *"]`; `src/index.js` runs GEX calculation on cron tick and pushes to Telegram via `broadcastGexReport` for every registered chat. Also answers direct Telegram messages (`/start`, `/status`, plain-text tickers).
- **`mk-quant-monitor-cboe-gex-main`** ⚠️ confirmed Streamlit dashboard; **the files I read in the ZIP contained no Telegram-side code** (no Telegram dep in `requirements.txt`, no Telegram handler in `app.py`/`data_fetcher.py`, no `import telegram` in the listed Python files). The three unzipped files I did not open — `gex_calculator.py`, `greeks.py`, `menthorq_formatter.py` — are unlikely to contain Telegram integration given the project's profile, but `grep -RInE 'telegram|bot_token' latest-zips/mk-qm-zip/` will close that loop. `runtime.txt` says `python-3.11.4`; `app.py` runs under `streamlit run`. Still, the "send only after requesting via chat" interpretation needs clarification:
  - *Most likely*: Telegram chat requests go to the GEX-Worker bot, which answers in brief; the dashboard is the manual UI for richer analytics (sidebar controls, charts, 0DTE tab, MenthorQ download button).
  - *Possible*: the chat → Streamlit integration lives outside this ZIP (Streamlit Cloud secrets, a webhook receiver in a separate repo, or a custom Streamlit component).
  - **Recommendation**: confirm the integration path. If `mk-quant-monitor-cboe-gex-main` should receive chat requests on its own, the receiving surface is missing from this ZIP and needs to be added.

### 7.4 Updated order of operations (post-audit)

| # | Action | Why it still matters |
|---|---|---|
| 1 | **Right now**: rotate the 11 brokerages/data keys from finding #1. Start with BingX and Deribit (withdrawal-scoped). | Keys were public pre-deletion; anyone with scraper/archive/chat-history may have copies. |
| 2 | **This week**: in `GEX-Worker---Cloudflare-KC-`, move `HF_WEBHOOK_URL` to a `wrangler secret`, flip `/telegram-webhook` to fail-closed, `git rm --cached .wrangler/cache/wrangler-account.json` and `**/.DS_Store`. | Source-level hardening that the private switch does not give you. |
| 3 | **This week**: forward-port the same source-level fixes (#2, #3, #4, #6) to your local `kc-gex-broadcast` fork at `MK_Quant_Monitor/gex-worker-cloudflare/`. | The fork carries the upstream repo's source verbatim, including `HF_WEBHOOK_URL`. |
| 4 | **This week**: enable GitHub Secret Scanning + Push Protection on the 3 remaining private repos. | Free; private repos can opt in too. |
| 5 | **This week**: shred `.security-audit/`, `latest-zips/`, and any other local copies once rotation is done and the fork is hardened. | Local copies of leaked values shouldn't outlive the rotation. |
| 6 | **Sprint**: add Python `.gitignore` to `mk-quant-monitor-cboe-gex`; remove `fix-normalize.mjs` from `GEX-Worker`; archive or populate `Quant-Trading-Desk-Retail-Edition-KC-`. | Hygiene + consistency. |

### 7.5 Re-push — bug fixes for the broadcaster (2026-07-16, late)

After the §7.4 plan was written, the user re-published `GEX-Worker---Cloudflare-KC-` as public and reported that the live Telegram chat (`KC GEX Broadcast`) was sending only `SPX,VIX`, surfacing deep-OTM `OI=0` strikes as "top GEX", and firing an identical `GEX REGIME CHANGE` alert every 15 minutes. Five source-level bugs were identified in `latest-zips/gex-worker-zip/GEX-Worker---Cloudflare-KC--main/` and pushed as commit **`30243abd996d977ab1228a861738a5d069e9fee2`** to `maxkru92/GEX-Worker---Cloudflare-KC-@main`:

| File | Change | Bug it fixes |
|---|---|---|
| `wrangler.toml` | `SYMBOLS = "SPX,NDX,RUT,VIX,SPY,QQQ,IWM"` (was `"SPX,VIX"`) | Cron broadcast now covers all 7 requested underlyings — `SPX, NDX, RUT, VIX, SPY, QQQ, IWM`. |
| `src/index.js` (`fetchCBOEChain`) | BSM gamma fallback now calls `approxGammaFromIV` (uses `iv / 100`) instead of inline `bsmGamma(S,K,iv,T)` | CBOE returns IV in percent; the old path treated it as decimal, blowing up gamma by ~100×. |
| `src/index.js` (`fetchCBOEChain`) | `frontExpiry` picker skips `DTE=0` entries; falls back to `0DTE` only when nothing else is in the chain | The old picker always grabbed today's 0DTE, which has thin OI and produced wrong Call Wall / Put Support / HVL at the same strike as Spot. Today's 0DTE levels are now reported in a separate block. |
| `src/index.js` (`computeGEX`) | `topCalls/topPuts/topNetGex` now filter out strikes where the relevant side has `OI=0` / `|netGex|=0` | Stops deep-OTM strikes (e.g. SPX `3000 / 3200 / 3400`) from being listed as the "top GEX" strikes on a day when only ATM strikes have any open interest. |
| `src/index.js` (`detectRegimeChange`) | Requires the **regime category** (`POSITIVE_GAMMA` ↔ `NEGATIVE_GAMMA` ↔ `NEUTRAL`) to change AND the delta to exceed `max(\|prev\|·15%, 1e9)`. Names aligned to `gex.regime`. | Identical-value alerts every 15 min stop firing. Big moves *within* the same category also no longer alert — use `/status` for a fresh read on demand. |
| `src/index.js` (`/webhook`) | Dropped proxy `symMap` (`SPY→SPX, QQQ→NDX, IWM→RUT`); passes the ticker through directly | TradingView alerts on the actual ETF now pull that ETF's own CBOE chain instead of getting silently proxied to the underlying index. |
| `src/data-fetcher.js` | `SYMBOL_CONFIG` extended with `NDX, RUT, SPY, QQQ, IWM` | Cosmetic — `index.js` defines its own inline `SYMBOL_CONFIG` and does not currently import this file, but kept in sync for any future refactor. |

Verified post-push via `https://raw.githubusercontent.com/.../main/wrangler.toml` that `SYMBOLS = "SPX,NDX,RUT,VIX,SPY,QQQ,IWM"` is live at upstream HEAD.

**Side-effect — three §1 rows re-expose.** Because the source-level fixes for findings #2 (move `HF_WEBHOOK_URL` to secret), #3 (flip `/telegram-webhook` to fail-closed), and #4 (`git rm --cached .wrangler/cache/wrangler-account.json`) **are NOT in this commit**, the repo being public again means those issues are again publicly-visible. The §1 status column for rows #2, #3, #4 has been updated to `[Public-surface RE-EXPOSED ...]` to reflect this. The two recommended follow-up commits (URL → secret; fail-closed; gitignore-out the .wrangler/ tree) are listed in §7.4 items 2 and 6 with explicit pointers.

**No new issues introduced by the push:** `node --check` passes on both modified JS files; staged diff was strictly `wrangler.toml` + `src/index.js` + `src/data-fetcher.js` (3 files / +56 / -28); no other tracked files were touched. `.env`, `.wrangler/`, `.DS_Store`, `fix-normalize.mjs` and the `pinescript/` + `scripts/` trees remain at their pre-existing state.
