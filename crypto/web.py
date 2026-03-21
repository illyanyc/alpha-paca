"""Lightweight FastAPI web dashboard — serves live trading data via WebSocket."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

app = FastAPI(title="Alpha-Paca Crypto", docs_url=None, redoc_url=None)

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "paca")
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
SESSION_COOKIE = "ap_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days

_state_ref: dict[str, Any] | None = None
_start_time_ref: float | None = None
_settings_ref: Any = None


def init_web(state: dict, start_time: float, settings: Any) -> None:
    """Bind shared state from the main trading loop."""
    global _state_ref, _start_time_ref, _settings_ref
    _state_ref = state
    _start_time_ref = start_time
    _settings_ref = settings


def _make_session_token(password: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), password.encode(), hashlib.sha256).hexdigest()[:48]


def _check_session(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    expected = _make_session_token(DASHBOARD_PASSWORD)
    return hmac.compare_digest(token, expected)


def _format_strategy_signals(raw: dict) -> dict:
    """Compact strategy signals for the dashboard."""
    out = {}
    for pair, strats in raw.items():
        buys = [s["name"] for s in strats if isinstance(s, dict) and s.get("signal") == "buy"]
        sells = [s["name"] for s in strats if isinstance(s, dict) and s.get("signal") == "sell"]
        if buys or sells:
            out[pair] = {"buy": buys, "sell": sells}
    return out


def _snapshot() -> dict[str, Any]:
    """Build a JSON-serializable snapshot of current state."""
    if not _state_ref:
        return {}
    s = _state_ref
    settings = _settings_ref
    uptime = int(time.time() - (_start_time_ref or time.time()))
    mode = s.get("trading_mode", "LIVE")

    prices = {}
    for pair, data in s.get("prices", {}).items():
        bid = data.get("bid", 0)
        ask = data.get("ask", 0)
        mid = data.get("mid", 0)
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 0
        history = s.get("price_history", {}).get(pair, [])
        prices[pair] = {
            "bid": bid, "ask": ask, "mid": mid,
            "spread_bps": round(spread_bps, 1),
            "history": history[-60:],
        }

    positions = []
    for p in s.get("positions", []):
        pair = p.get("pair", p.get("symbol", "?"))
        side = p.get("side", "long")
        qty = float(p.get("qty", 0))
        entry = float(p.get("avg_entry_price", 0))
        current = float(p.get("current_price", 0))
        pnl = float(p.get("unrealized_pnl", p.get("unrealized_pl", 0)))
        pnl_pct = float(p.get("unrealized_pnl_pct", 0))
        if pnl_pct == 0 and entry * qty > 0:
            pnl_pct = (pnl / (entry * qty) * 100)
        mv = float(p.get("market_value", qty * current))
        positions.append({
            "pair": pair, "side": side, "qty": qty, "entry": entry, "current": current,
            "pnl": round(pnl, 4), "pnl_pct": round(pnl_pct, 2), "value": round(mv, 2),
        })

    tech = {}
    for pair, t in s.get("tech_signals", {}).items():
        tech[pair] = {
            "signal": t.get("signal", "—"),
            "score": round(t.get("score", 0), 2),
            "details": t.get("details", "")[:50],
        }

    fund = {}
    for pair, f in s.get("fund_signals", {}).items():
        fund[pair] = {
            "signal": f.get("signal", "—"),
            "score": round(f.get("score", 0), 2),
        }

    trades = []
    for t in s.get("recent_trades", [])[-20:]:
        pnl_val = t.get("pnl", 0) or 0
        trades.append({
            "pair": t.get("pair", "?"),
            "side": t.get("side", "?"),
            "qty": float(t.get("qty", 0)),
            "price": float(t.get("entry_price", t.get("price", 0))),
            "pnl": float(pnl_val),
            "reasoning": (t.get("reasoning", "") or "")[:40],
            "time": str(t.get("opened_at", ""))[:19],
        })

    portfolio = s.get("portfolio", {})
    news = s.get("news_data", {})
    news_sentiment = news.get("overall_sentiment", "—") if isinstance(news, dict) else "—"
    news_score = news.get("overall_score", 0) if isinstance(news, dict) else 0

    healing = []
    for evt in s.get("healing_events", [])[-20:]:
        healing.append({
            "agent": evt.get("agent", "?"),
            "message": evt.get("message", ""),
            "severity": evt.get("severity", "info"),
            "outcome": evt.get("outcome", ""),
            "action": evt.get("action", ""),
            "confidence": evt.get("confidence", 0),
            "timestamp": evt.get("timestamp", ""),
        })

    exchange_status = s.get("exchange_status", "checking")
    exchange_error = s.get("exchange_error", "")

    trading_cfg = {}
    if settings:
        trading_cfg = {
            "max_capital": settings.crypto.max_capital,
            "risk_per_trade_pct": settings.crypto.risk_per_trade_pct,
            "max_position_pct": settings.crypto.max_position_pct,
            "max_drawdown_pct": settings.crypto.max_drawdown_pct,
            "max_total_exposure_pct": settings.crypto.max_total_exposure_pct,
            "confidence_threshold": settings.crypto.confidence_threshold,
            "stop_loss_pct": settings.crypto.stop_loss_pct,
            "take_profit_pct": settings.crypto.take_profit_pct,
            "pairs": settings.crypto.pairs,
        }

    return {
        "mode": mode,
        "uptime": uptime,
        "ts": datetime.now(timezone.utc).isoformat(),
        "exchange": {"status": exchange_status, "error": exchange_error},
        "trading_settings": trading_cfg,
        "portfolio": {
            "nav": round(portfolio.get("nav", 0), 2),
            "cash": round(portfolio.get("cash", 0), 2),
            "exposure_pct": round(portfolio.get("total_exposure_pct", 0), 1),
            "unrealized_pnl": round(portfolio.get("unrealized_pnl", 0), 2),
            "drawdown_pct": round(portfolio.get("drawdown_pct", 0), 1),
        },
        "prices": prices,
        "positions": positions,
        "tech_signals": tech,
        "fund_signals": fund,
        "news": {"sentiment": news_sentiment, "score": round(news_score, 2)},
        "recent_trades": trades,
        "agents": s.get("agent_statuses", {}),
        "healing": healing,
        "agent_log": s.get("agent_log", [])[-30:],
        "strategy_signals": _format_strategy_signals(s.get("strategy_signals", {})),
        "backtest": s.get("backtest_results", {}),
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_HTML


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    password = form.get("password", "")
    if password == DASHBOARD_PASSWORD:
        token = _make_session_token(DASHBOARD_PASSWORD)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return response
    return HTMLResponse(LOGIN_HTML.replace("<!-- ERROR -->", '<p class="error">Wrong password</p>'), status_code=401)


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _check_session(request):
        return RedirectResponse(url="/login", status_code=303)
    return DASHBOARD_HTML


@app.get("/api/state")
async def api_state(request: Request):
    if not _check_session(request):
        raise HTTPException(status_code=401)
    return _snapshot()


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not _check_session(request):
        return RedirectResponse(url="/login", status_code=303)
    return SETTINGS_HTML


@app.post("/api/settings/exchange")
async def save_exchange_keys(request: Request):
    """Hot-swap Coinbase API keys, persist to Redis."""
    if not _check_session(request):
        raise HTTPException(status_code=401)
    body = await request.json()
    api_key = (body.get("api_key") or "").strip()
    api_secret = (body.get("api_secret") or "").strip()

    if not api_key or not api_secret:
        return JSONResponse({"status": "error", "error": "API key and secret are required"}, status_code=400)

    from main import reload_coinbase_keys
    result = await reload_coinbase_keys(api_key, api_secret)
    status_code = 200 if result["status"] == "connected" else 400
    return JSONResponse(result, status_code=status_code)


@app.post("/api/settings/trading")
async def save_trading_settings(request: Request):
    """Update trading parameters, persist to Redis."""
    if not _check_session(request):
        raise HTTPException(status_code=401)
    body = await request.json()

    from main import update_trading_settings
    result = await update_trading_settings(body)
    return JSONResponse(result)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = _snapshot()
            await ws.send_json(data)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ── Inline HTML Templates ──────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alpha-Paca Crypto - Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e17;color:#e0e6ed;font-family:-apple-system,system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#131a2b;border:1px solid #1e2d4a;border-radius:16px;padding:40px;width:340px;text-align:center}
.card h1{font-size:28px;margin-bottom:8px}
.card .sub{color:#6b7b9e;margin-bottom:24px;font-size:14px}
.card input{width:100%;padding:12px 16px;background:#0a0e17;border:1px solid #1e2d4a;border-radius:8px;
color:#e0e6ed;font-size:16px;margin-bottom:16px;outline:none}
.card input:focus{border-color:#00d4aa}
.card button{width:100%;padding:12px;background:linear-gradient(135deg,#00d4aa,#00b4d8);
border:none;border-radius:8px;color:#0a0e17;font-size:16px;font-weight:700;cursor:pointer}
.card button:hover{opacity:.9}
.error{color:#ff6b6b;font-size:13px;margin-bottom:12px}
.emoji{font-size:48px;margin-bottom:12px}
</style></head><body>
<form class="card" method="POST" action="/login">
<div class="emoji">🦙</div>
<h1>Alpha-Paca</h1>
<p class="sub">Crypto Trading Dashboard</p>
<!-- ERROR -->
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Sign In</button>
</form></body></html>"""


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Alpha-Paca Crypto</title>
<style>
:root{
--bg:#0a0e17;--card:#131a2b;--border:#1e2d4a;--text:#e0e6ed;--dim:#6b7b9e;
--green:#00d4aa;--red:#ff6b6b;--yellow:#ffd93d;--blue:#00b4d8;--purple:#a78bfa;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'SF Mono',SFMono-Regular,Menlo,monospace;
font-size:13px;-webkit-font-smoothing:antialiased;overflow-x:hidden}
.container{max-width:960px;margin:0 auto;padding:12px}
.header{text-align:center;padding:16px 0 8px;border-bottom:1px solid var(--border);margin-bottom:12px}
.header h1{font-size:20px;letter-spacing:2px}
.header .meta{color:var(--dim);font-size:11px;margin-top:4px}
.mode-live{color:var(--red);font-weight:700} .mode-paper{color:var(--green);font-weight:700}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700}
.badge-live{background:rgba(255,107,107,.15);color:var(--red);border:1px solid var(--red)}
.badge-paper{background:rgba(0,212,170,.15);color:var(--green);border:1px solid var(--green)}
.section{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;margin-bottom:12px}
.section-title{font-size:12px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.stat{text-align:center;padding:8px;background:var(--bg);border-radius:8px}
.stat .label{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.stat .value{font-size:18px;font-weight:700;margin-top:2px}
.green{color:var(--green)} .red{color:var(--red)} .yellow{color:var(--yellow)} .blue{color:var(--blue)}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--dim);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;text-transform:uppercase}
td{padding:6px 8px;border-bottom:1px solid rgba(30,45,74,.4)}
tr:last-child td{border-bottom:none}
.r{text-align:right}
.spark{font-size:14px;letter-spacing:-1px;line-height:1;color:var(--blue)}
.signal-buy{color:var(--green);font-weight:700}
.signal-sell{color:var(--red);font-weight:700}
.signal-neutral{color:var(--yellow)}
.agent-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
.dot-healthy{background:var(--green)} .dot-running{background:var(--blue);animation:pulse 1s infinite}
.dot-idle{background:var(--dim)} .dot-error{background:var(--red)}
.dot-standby{background:var(--purple)}
.dot-healing{background:var(--yellow);animation:pulse 1.5s infinite}
.dot-circuit_open{background:#ff8c00;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.tl-entry{padding:3px 0;border-bottom:1px solid rgba(30,45,74,.3)}
.tl-entry:last-child{border-bottom:none}
.tl-time{color:var(--dim);margin-right:6px;font-size:10px}
.tl-agent{font-weight:700;margin-right:6px}
.tl-news_scout{color:#ffd93d} .tl-technical_analyst{color:#00b4d8} .tl-fundamental_analyst{color:#a78bfa}
.tl-orchestrator{color:#00d4aa} .tl-risk_validator{color:#ff8c00} .tl-order_executor{color:#ff6b6b}
.agents-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px}
.agent-item{display:flex;align-items:center;padding:4px 8px;background:var(--bg);border-radius:6px;font-size:11px}
.conn{position:fixed;top:8px;right:8px;font-size:10px;padding:3px 8px;border-radius:4px;z-index:99}
.conn-ok{background:rgba(0,212,170,.2);color:var(--green)}
.conn-lost{background:rgba(255,107,107,.2);color:var(--red)}
.no-data{color:var(--dim);font-style:italic;padding:8px;text-align:center}
.side-buy{color:var(--green);font-weight:700} .side-sell{color:var(--red);font-weight:700}
@media(max-width:500px){.grid3{grid-template-columns:1fr 1fr}.stat .value{font-size:15px}table{font-size:11px}th,td{padding:4px 6px}}
.logout{position:fixed;top:8px;left:8px;font-size:10px;color:var(--dim);text-decoration:none;padding:3px 8px;border-radius:4px;background:var(--card);border:1px solid var(--border)}
.logout:hover{color:var(--text)}
.settings-link{position:fixed;top:8px;left:72px;font-size:10px;color:var(--dim);text-decoration:none;padding:3px 8px;border-radius:4px;background:var(--card);border:1px solid var(--border)}
.settings-link:hover{color:var(--text)}
.exch-banner{padding:8px 12px;border-radius:8px;margin-bottom:12px;font-size:12px;display:flex;align-items:center;gap:8px}
.exch-ok{background:rgba(0,212,170,.1);border:1px solid rgba(0,212,170,.3);color:var(--green)}
.exch-fail{background:rgba(255,107,107,.1);border:1px solid rgba(255,107,107,.3);color:var(--red)}
.exch-checking{background:rgba(255,217,61,.1);border:1px solid rgba(255,217,61,.3);color:var(--yellow)}
</style></head><body>
<div id="conn" class="conn conn-lost">CONNECTING</div>
<a href="/logout" class="logout">Logout</a>
<a href="/settings" class="settings-link">⚙️ Settings</a>
<div class="container">

<div class="header">
<h1>🦙 ALPHA-PACA CRYPTO</h1>
<div class="meta">
<span id="mode-badge" class="badge badge-paper">PAPER</span>
&nbsp; Uptime: <span id="uptime">00:00:00</span>
&nbsp; <span id="ts"></span>
</div>
</div>

<div id="exch-banner" class="exch-banner exch-checking" style="display:none">
<span id="exch-icon">⏳</span>
<span id="exch-msg">Checking Coinbase connection...</span>
<a href="/settings" style="margin-left:auto;color:inherit;font-weight:700;text-decoration:underline">Fix →</a>
</div>

<div class="section">
<div class="section-title">📊 Portfolio</div>
<div class="grid3" id="portfolio-stats">
<div class="stat"><div class="label">NAV</div><div class="value" id="nav">$0</div></div>
<div class="stat"><div class="label">Cash</div><div class="value" id="cash">$0</div></div>
<div class="stat"><div class="label">Exposure</div><div class="value green" id="exposure">0%</div></div>
<div class="stat"><div class="label">Unrealized</div><div class="value" id="unrealized">$0</div></div>
<div class="stat"><div class="label">Drawdown</div><div class="value green" id="drawdown">0%</div></div>
<div class="stat"><div class="label">Positions</div><div class="value" id="pos-count">0</div></div>
</div>
</div>

<div class="section">
<div class="section-title">💰 Live Prices</div>
<div style="overflow-x:auto"><table>
<thead><tr><th>Pair</th><th class="r">Mid</th><th class="r">Spread</th><th>Chart</th></tr></thead>
<tbody id="prices-body"></tbody>
</table></div>
</div>

<div class="section" id="positions-section" style="display:none">
<div class="section-title">📦 Open Positions</div>
<div style="overflow-x:auto"><table>
<thead><tr><th>Pair</th><th>Side</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Current</th><th class="r">P&L</th></tr></thead>
<tbody id="positions-body"></tbody>
</table></div>
</div>

<div class="section">
<div class="section-title">📡 Signals</div>
<div style="overflow-x:auto"><table>
<thead><tr><th>Pair</th><th>Tech</th><th class="r">Score</th><th>Fund</th><th class="r">Score</th></tr></thead>
<tbody id="signals-body"></tbody>
</table></div>
<div id="news-sentiment" style="margin-top:8px;font-size:11px;color:var(--dim)"></div>
</div>

<div class="section">
<div class="section-title">📋 Recent Trades</div>
<div style="overflow-x:auto"><table>
<thead><tr><th>Time</th><th>Side</th><th>Pair</th><th class="r">Price</th><th class="r">P&L</th></tr></thead>
<tbody id="trades-body"></tbody>
</table></div>
<div id="no-trades" class="no-data">No trades yet</div>
</div>

<div class="section">
<div class="section-title">🤖 Agents</div>
<div class="agents-grid" id="agents-grid"></div>
</div>

<div class="section" id="strategy-section">
<div class="section-title">🎯 Strategy Signals</div>
<div id="strategy-body" style="font-size:11px"></div>
</div>

<div class="section" id="thinking-section">
<div class="section-title">🧠 Agent Thinking</div>
<div id="thinking-log" style="max-height:280px;overflow-y:auto;font-size:11px;line-height:1.7"></div>
</div>

<div class="section" id="healing-section" style="display:none">
<div class="section-title">🩺 Self-Healing</div>
<div style="overflow-x:auto"><table>
<thead><tr><th>Time</th><th>Agent</th><th>Status</th><th>Event</th></tr></thead>
<tbody id="healing-body"></tbody>
</table></div>
</div>

</div>

<script>
const $ = id => document.getElementById(id);
const SPARK = '▁▂▃▄▅▆▇█';

function sparkline(arr) {
  if (!arr || arr.length < 2) return '—';
  const vals = arr.slice(-30);
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const rng = mx - mn || 1;
  return vals.map(v => SPARK[Math.min(Math.floor((v-mn)/rng*7),7)]).join('');
}

function fmt(n, decimals) {
  if (n >= 1000) return '$' + n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  if (n >= 1) return '$' + n.toFixed(decimals || 4);
  return '$' + n.toFixed(6);
}

function pnlClass(v) { return v > 0 ? 'green' : v < 0 ? 'red' : ''; }
function signalClass(s) {
  s = (s||'').toLowerCase();
  if (s.includes('buy')) return 'signal-buy';
  if (s.includes('sell')) return 'signal-sell';
  return 'signal-neutral';
}

function uptime(sec) {
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  return [h,m,s].map(x => String(x).padStart(2,'0')).join(':');
}

const AGENT_ICONS = {
  news_scout:'📰', technical_analyst:'📈', fundamental_analyst:'🔬',
  orchestrator:'🧠', risk_validator:'🛡️', order_executor:'⚡'
};

function render(d) {
  if (!d || !d.portfolio) return;

  // Exchange status banner
  const ab = $('exch-banner');
  const exch = d.exchange || {};
  if (exch.status === 'connected') {
    ab.style.display = 'flex';
    ab.className = 'exch-banner exch-ok';
    $('exch-icon').textContent = '✅';
    $('exch-msg').textContent = 'Coinbase connected — trading enabled';
  } else if (exch.status === 'unauthorized') {
    ab.style.display = 'flex';
    ab.className = 'exch-banner exch-fail';
    $('exch-icon').textContent = '🔴';
    $('exch-msg').textContent = 'Coinbase unauthorized — ' + (exch.error || 'check API keys');
  } else if (exch.status === 'checking') {
    ab.style.display = 'flex';
    ab.className = 'exch-banner exch-checking';
    $('exch-icon').textContent = '⏳';
    $('exch-msg').textContent = 'Checking Coinbase connection...';
  } else {
    ab.style.display = 'none';
  }

  // Mode
  const badge = $('mode-badge');
  badge.textContent = d.mode;
  badge.className = 'badge badge-' + d.mode.toLowerCase();
  $('uptime').textContent = uptime(d.uptime);
  $('ts').textContent = (d.ts||'').replace('T',' ').slice(0,19) + ' UTC';

  // Portfolio
  const p = d.portfolio;
  $('nav').textContent = '$' + p.nav.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  $('cash').textContent = '$' + p.cash.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const expEl = $('exposure');
  expEl.textContent = p.exposure_pct.toFixed(1) + '%';
  expEl.className = 'value ' + (p.exposure_pct < 70 ? 'green' : p.exposure_pct < 90 ? 'yellow' : 'red');
  const unrEl = $('unrealized');
  unrEl.textContent = '$' + (p.unrealized_pnl >= 0 ? '+' : '') + p.unrealized_pnl.toFixed(2);
  unrEl.className = 'value ' + pnlClass(p.unrealized_pnl);
  const ddEl = $('drawdown');
  ddEl.textContent = p.drawdown_pct.toFixed(1) + '%';
  ddEl.className = 'value ' + (p.drawdown_pct < 5 ? 'green' : p.drawdown_pct < 10 ? 'yellow' : 'red');
  $('pos-count').textContent = (d.positions||[]).length;

  // Prices
  let priceHTML = '';
  Object.keys(d.prices||{}).sort().forEach(pair => {
    const px = d.prices[pair];
    const spClass = px.spread_bps < 20 ? 'green' : px.spread_bps < 50 ? 'yellow' : 'red';
    priceHTML += `<tr><td><b>${pair}</b></td><td class="r">${fmt(px.mid)}</td>` +
      `<td class="r ${spClass}">${px.spread_bps.toFixed(1)}bps</td>` +
      `<td class="spark">${sparkline(px.history)}</td></tr>`;
  });
  $('prices-body').innerHTML = priceHTML || '<tr><td colspan="4" class="no-data">Loading...</td></tr>';

  // Positions
  const posSection = $('positions-section');
  if (d.positions && d.positions.length > 0) {
    posSection.style.display = '';
    let posHTML = '';
    d.positions.forEach(pos => {
      const sideTag = (pos.side||'long').toUpperCase();
      const sideCls = sideTag === 'SHORT' ? 'neg' : 'pos';
      posHTML += `<tr><td><b>${pos.pair}</b></td><td class="${sideCls}" style="font-weight:600">${sideTag}</td><td class="r">${pos.qty.toFixed(6)}</td>` +
        `<td class="r">${fmt(pos.entry)}</td><td class="r">${fmt(pos.current)}</td>` +
        `<td class="r ${pnlClass(pos.pnl)}">$${pos.pnl >= 0 ? '+' : ''}${pos.pnl.toFixed(2)} (${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(1)}%)</td></tr>`;
    });
    $('positions-body').innerHTML = posHTML;
  } else {
    posSection.style.display = 'none';
  }

  // Signals
  const allPairs = [...new Set([...Object.keys(d.tech_signals||{}), ...Object.keys(d.fund_signals||{})])].sort();
  let sigHTML = '';
  allPairs.forEach(pair => {
    const t = (d.tech_signals||{})[pair] || {};
    const f = (d.fund_signals||{})[pair] || {};
    sigHTML += `<tr><td><b>${pair}</b></td>` +
      `<td class="${signalClass(t.signal)}">${t.signal||'—'}</td><td class="r">${(t.score||0).toFixed(2)}</td>` +
      `<td class="${signalClass(f.signal)}">${f.signal||'—'}</td><td class="r">${(f.score||0).toFixed(2)}</td></tr>`;
  });
  $('signals-body').innerHTML = sigHTML || '<tr><td colspan="5" class="no-data">Loading...</td></tr>';
  const ns = d.news || {};
  $('news-sentiment').innerHTML = `📰 News: <span class="${signalClass(ns.sentiment)}">${ns.sentiment}</span> (score: ${(ns.score||0).toFixed(2)})`;

  // Trades
  const trades = d.recent_trades || [];
  if (trades.length > 0) {
    $('no-trades').style.display = 'none';
    let tHTML = '';
    trades.slice().reverse().slice(0,10).forEach(t => {
      tHTML += `<tr><td style="white-space:nowrap">${t.time}</td>` +
        `<td class="side-${t.side.toLowerCase()}">${t.side}</td>` +
        `<td>${t.pair}</td><td class="r">${fmt(t.price)}</td>` +
        `<td class="r ${pnlClass(t.pnl)}">$${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}</td></tr>`;
    });
    $('trades-body').innerHTML = tHTML;
  } else {
    $('no-trades').style.display = '';
    $('trades-body').innerHTML = '';
  }

  // Agents
  let agentHTML = '';
  Object.entries(d.agents||{}).forEach(([name, status]) => {
    const icon = AGENT_ICONS[name] || '🤖';
    agentHTML += `<div class="agent-item"><span class="agent-dot dot-${status}"></span>${icon} ${name.replace(/_/g,' ')}</div>`;
  });
  $('agents-grid').innerHTML = agentHTML;

  // Strategy Signals
  const strats = d.strategy_signals || {};
  const sBody = $('strategy-body');
  const sPairs = Object.keys(strats);
  if (sPairs.length > 0) {
    let sHTML = '<div style="display:flex;flex-wrap:wrap;gap:6px">';
    sPairs.forEach(pair => {
      const s = strats[pair];
      const buys = (s.buy||[]).map(n => '<span style="color:var(--green)">▲'+n+'</span>').join(' ');
      const sells = (s.sell||[]).map(n => '<span style="color:var(--red)">▼'+n+'</span>').join(' ');
      sHTML += `<div style="background:var(--bg);padding:4px 8px;border-radius:6px;border:1px solid var(--border)"><b>${pair}</b> ${buys} ${sells}</div>`;
    });
    const bt = d.backtest || {};
    if (bt.aggregate) {
      sHTML += '</div><div style="margin-top:8px;color:var(--dim)">Backtest: ';
      bt.aggregate.forEach(a => {
        const col = a.sharpe > 0.5 ? 'var(--green)' : (a.sharpe > 0 ? 'var(--yellow)' : 'var(--red)');
        sHTML += `<span style="color:${col}">${a.name}(S=${a.sharpe.toFixed(1)},W=${(a.win_rate*100).toFixed(0)}%,w=${(a.weight*100).toFixed(0)}%)</span> `;
      });
      sHTML += '</div>';
    }
    sBody.innerHTML = sHTML;
  } else {
    sBody.innerHTML = '<span style="color:var(--dim)">Computing strategies...</span>';
  }

  // Agent Thinking
  const alog = d.agent_log || [];
  const tDiv = $('thinking-log');
  if (alog.length > 0) {
    const ICONS = {news_scout:'📰',technical_analyst:'📈',fundamental_analyst:'🔬',orchestrator:'🧠',risk_validator:'🛡️',order_executor:'⚡'};
    let tHTML = '';
    alog.slice(-25).forEach(e => {
      const ts = (e.ts||'').slice(11,19);
      const icon = ICONS[e.agent]||'🤖';
      tHTML += `<div class="tl-entry"><span class="tl-time">${ts}</span>` +
        `<span class="tl-agent tl-${e.agent}">${icon} ${e.agent.replace(/_/g,' ')}</span>` +
        `<span>${e.step}</span></div>`;
    });
    tDiv.innerHTML = tHTML;
    tDiv.scrollTop = tDiv.scrollHeight;
  } else {
    tDiv.innerHTML = '<div style="color:var(--dim);text-align:center;padding:12px">Waiting for first agent cycle...</div>';
  }

  // Healing
  const healing = d.healing || [];
  const healSec = $('healing-section');
  if (healing.length > 0) {
    healSec.style.display = '';
    const OUTCOME_ICONS = {healed:'✅',retrying:'🔄',circuit_open:'🔶',skipped:'⏭️',retry_failed:'❌'};
    const SEV_CLASS = {critical:'red',warning:'yellow',transient:'blue',info:'green'};
    let hHTML = '';
    healing.slice().reverse().slice(0,8).forEach(h => {
      const ts = (h.timestamp||'').slice(11,19);
      const oIcon = OUTCOME_ICONS[h.outcome] || '';
      const sClass = SEV_CLASS[h.severity] || 'dim';
      hHTML += `<tr><td style="white-space:nowrap">${ts}</td><td>${h.agent}</td>` +
        `<td>${oIcon}</td><td class="${sClass}">${h.message}</td></tr>`;
    });
    $('healing-body').innerHTML = hHTML;
  } else {
    healSec.style.display = 'none';
  }
}

// WebSocket connection with auto-reconnect
let ws, reconnectTimer;
const connEl = $('conn');

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => { connEl.textContent = 'LIVE'; connEl.className = 'conn conn-ok'; };
  ws.onclose = () => { connEl.textContent = 'RECONNECTING'; connEl.className = 'conn conn-lost'; reconnectTimer = setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch(err) { console.error(err); } };
}

connect();

// Fallback: poll /api/state every 5s if WS fails
setInterval(async () => {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  try {
    const r = await fetch('/api/state', {credentials:'same-origin'});
    if (r.ok) render(await r.json());
  } catch(e) {}
}, 5000);
</script>
</body></html>"""


SETTINGS_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Alpha-Paca Crypto - Settings</title>
<style>
:root{--bg:#0a0e17;--card:#131a2b;--border:#1e2d4a;--text:#e0e6ed;--dim:#6b7b9e;
--green:#00d4aa;--red:#ff6b6b;--yellow:#ffd93d;--blue:#00b4d8}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'SF Mono',SFMono-Regular,Menlo,monospace;
font-size:13px;-webkit-font-smoothing:antialiased}
.container{max-width:600px;margin:0 auto;padding:16px}
.header{display:flex;align-items:center;gap:12px;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:20px}
.header h1{font-size:18px;letter-spacing:1px}
.back{color:var(--dim);text-decoration:none;font-size:20px;padding:4px 8px}
.back:hover{color:var(--text)}
.section{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.section-title{font-size:12px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;display:flex;align-items:center;gap:6px}
.field{margin-bottom:12px}
.field label{display:block;font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.field input,.field select{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;
color:var(--text);font-family:inherit;font-size:13px;outline:none}
.field input:focus,.field select:focus{border-color:var(--green)}
.field input[type="password"]{letter-spacing:2px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.toggle-row{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.toggle{position:relative;width:44px;height:24px;background:var(--border);border-radius:12px;cursor:pointer;transition:.2s}
.toggle.on{background:var(--green)}
.toggle::after{content:'';position:absolute;top:2px;left:2px;width:20px;height:20px;background:#fff;border-radius:50%;transition:.2s}
.toggle.on::after{left:22px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:10px 20px;border:none;border-radius:8px;font-family:inherit;font-size:13px;font-weight:700;cursor:pointer;transition:.15s}
.btn-primary{background:linear-gradient(135deg,var(--green),var(--blue));color:var(--bg)}
.btn-primary:hover{opacity:.9}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.status-msg{margin-top:12px;padding:8px 12px;border-radius:8px;font-size:12px;display:none}
.status-ok{display:block;background:rgba(0,212,170,.1);border:1px solid rgba(0,212,170,.3);color:var(--green)}
.status-err{display:block;background:rgba(255,107,107,.1);border:1px solid rgba(255,107,107,.3);color:var(--red)}
.status-loading{display:block;background:rgba(255,217,61,.1);border:1px solid rgba(255,217,61,.3);color:var(--yellow)}
.current-status{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;margin-bottom:12px;font-size:12px}
.cs-ok{background:rgba(0,212,170,.08);color:var(--green)}
.cs-fail{background:rgba(255,107,107,.08);color:var(--red)}
.cs-checking{background:rgba(255,217,61,.08);color:var(--yellow)}
.hint{font-size:11px;color:var(--dim);margin-top:4px}
.divider{border:none;border-top:1px solid var(--border);margin:16px 0}
</style></head><body>
<div class="container">
<div class="header">
<a href="/" class="back">←</a>
<h1>⚙️ Settings</h1>
</div>

<div class="section">
<div class="section-title">🔑 Coinbase API Keys</div>
<div id="current-status" class="current-status cs-checking">⏳ Checking...</div>

<div class="field">
<label>API Key</label>
<input type="text" id="api-key" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" autocomplete="off" spellcheck="false">
</div>
<div class="field">
<label>API Secret</label>
<input type="password" id="api-secret" placeholder="Enter Coinbase API secret" autocomplete="off">
</div>

<button class="btn btn-primary" id="save-btn" onclick="saveKeys()">
💾 Save & Test Connection
</button>
<div id="status-msg" class="status-msg"></div>
<div class="hint" style="margin-top:8px">Keys are saved to Redis and persist across redeploys.</div>
</div>

<div class="section">
<div class="section-title">💰 Trading Parameters</div>

<div class="field">
<label>Max Investable Capital ($)</label>
<input type="number" id="t-max-capital" min="0" step="100" placeholder="1000">
<div class="hint">Maximum USD the system can deploy — even if your Coinbase account has more</div>
</div>

<div class="row2">
<div class="field">
<label>Risk per Trade (%)</label>
<input type="number" id="t-risk-per-trade" min="0.1" max="10" step="0.1" placeholder="2.0">
</div>
<div class="field">
<label>Max Position (%)</label>
<input type="number" id="t-max-position" min="1" max="100" step="1" placeholder="30">
</div>
</div>

<div class="row2">
<div class="field">
<label>Max Drawdown (%)</label>
<input type="number" id="t-max-drawdown" min="1" max="50" step="1" placeholder="10">
</div>
<div class="field">
<label>Max Total Exposure (%)</label>
<input type="number" id="t-max-exposure" min="10" max="100" step="5" placeholder="90">
</div>
</div>

<div class="row2">
<div class="field">
<label>Stop-Loss (%)</label>
<input type="number" id="t-stop-loss" min="1" max="50" step="0.5" placeholder="5.0">
<div class="hint">Auto-sell if position drops below this %</div>
</div>
<div class="field">
<label>Take-Profit (%)</label>
<input type="number" id="t-take-profit" min="1" max="100" step="0.5" placeholder="12.0">
<div class="hint">Auto-sell if position gains exceed this %</div>
</div>
</div>

<div class="field">
<label>Confidence Threshold (0-1)</label>
<input type="number" id="t-confidence" min="0.1" max="1.0" step="0.05" placeholder="0.5">
<div class="hint">Minimum AI confidence to execute a trade. Lower = more trades, higher = more selective</div>
</div>

<div class="field">
<label>Trading Pairs (comma-separated)</label>
<input type="text" id="t-pairs" placeholder="BTC/USD,ETH/USD,SOL/USD" spellcheck="false">
</div>

<button class="btn btn-primary" id="save-trading-btn" onclick="saveTradingSettings()">
💾 Save Trading Settings
</button>
<div id="trading-status-msg" class="status-msg"></div>
<div class="hint" style="margin-top:8px">Settings are saved to Redis — applied immediately and persist across redeploys.</div>
</div>

<div class="section">
<div class="section-title">ℹ️ Help</div>
<div style="font-size:12px;color:var(--dim);line-height:1.6">
<p>• Get CDP API keys from <a href="https://portal.cdp.coinbase.com/access/api" target="_blank" style="color:var(--blue)">portal.cdp.coinbase.com</a></p>
<p>• Key name format: <code>organizations/{org_id}/apiKeys/{key_id}</code></p>
<p>• Secret: PEM EC private key (use <code>\n</code> for newlines in env var)</p>
<p>• <b>Stop-Loss</b>: auto-sells a position if unrealized loss exceeds threshold</p>
<p>• <b>Take-Profit</b>: auto-sells a position if unrealized gain exceeds threshold</p>
<p>• All settings saved here are stored in <b>Redis</b> and override env vars on startup</p>
</div>
</div>

</div>
<script>
const $ = id => document.getElementById(id);

async function loadStatus() {
  try {
    const r = await fetch('/api/state', {credentials:'same-origin'});
    if (!r.ok) return;
    const d = await r.json();

    const cs = $('current-status');
    const exch = d.exchange || {};
    if (exch.status === 'connected') {
      cs.className = 'current-status cs-ok'; cs.innerHTML = '✅ Coinbase connected — trading enabled';
    } else if (exch.status === 'unauthorized') {
      cs.className = 'current-status cs-fail'; cs.innerHTML = '🔴 Unauthorized — ' + (exch.error || 'check API keys');
    } else {
      cs.className = 'current-status cs-checking'; cs.innerHTML = '⏳ Checking...';
    }

    const ts = d.trading_settings || {};
    if (ts.max_capital !== undefined) $('t-max-capital').value = ts.max_capital;
    if (ts.risk_per_trade_pct !== undefined) $('t-risk-per-trade').value = ts.risk_per_trade_pct;
    if (ts.max_position_pct !== undefined) $('t-max-position').value = ts.max_position_pct;
    if (ts.max_drawdown_pct !== undefined) $('t-max-drawdown').value = ts.max_drawdown_pct;
    if (ts.max_total_exposure_pct !== undefined) $('t-max-exposure').value = ts.max_total_exposure_pct;
    if (ts.confidence_threshold !== undefined) $('t-confidence').value = ts.confidence_threshold;
    if (ts.stop_loss_pct !== undefined) $('t-stop-loss').value = ts.stop_loss_pct;
    if (ts.take_profit_pct !== undefined) $('t-take-profit').value = ts.take_profit_pct;
    if (ts.pairs) $('t-pairs').value = ts.pairs;
  } catch(e) { console.error(e); }
}

async function saveKeys() {
  const btn = $('save-btn'), msg = $('status-msg');
  const apiKey = $('api-key').value.trim();
  const apiSecret = $('api-secret').value.trim();

  if (!apiKey || !apiSecret) { msg.className='status-msg status-err'; msg.textContent='Both API key and secret are required'; return; }

  btn.disabled = true; btn.textContent = '⏳ Testing...';
  msg.className = 'status-msg status-loading'; msg.textContent = 'Connecting to Coinbase...';

  try {
    const r = await fetch('/api/settings/exchange', {
      method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin',
      body: JSON.stringify({api_key:apiKey, api_secret:apiSecret}),
    });
    const data = await r.json();
    if (data.status === 'connected') {
      msg.className='status-msg status-ok'; msg.textContent='✅ Connected to Coinbase!';
      $('api-secret').value = ''; loadStatus();
    } else {
      msg.className='status-msg status-err'; msg.textContent='❌ ' + (data.error || 'unauthorized');
    }
  } catch(e) {
    msg.className='status-msg status-err'; msg.textContent='❌ Network error: ' + e.message;
  } finally { btn.disabled=false; btn.textContent='💾 Save & Test Connection'; }
}

async function saveTradingSettings() {
  const btn = $('save-trading-btn'), msg = $('trading-status-msg');
  const body = {
    max_capital: parseFloat($('t-max-capital').value) || undefined,
    risk_per_trade_pct: parseFloat($('t-risk-per-trade').value) || undefined,
    max_position_pct: parseFloat($('t-max-position').value) || undefined,
    max_drawdown_pct: parseFloat($('t-max-drawdown').value) || undefined,
    max_total_exposure_pct: parseFloat($('t-max-exposure').value) || undefined,
    confidence_threshold: parseFloat($('t-confidence').value) || undefined,
    stop_loss_pct: parseFloat($('t-stop-loss').value) || undefined,
    take_profit_pct: parseFloat($('t-take-profit').value) || undefined,
    pairs: $('t-pairs').value.trim() || undefined,
  };
  Object.keys(body).forEach(k => body[k] === undefined && delete body[k]);

  if (Object.keys(body).length === 0) { msg.className='status-msg status-err'; msg.textContent='No changes to save'; return; }

  btn.disabled = true; btn.textContent = '⏳ Saving...';
  msg.className='status-msg status-loading'; msg.textContent='Saving...';

  try {
    const r = await fetch('/api/settings/trading', {
      method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin',
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      msg.className='status-msg status-ok'; msg.textContent='✅ Saved: ' + (data.updated||[]).join(', ');
      loadStatus();
    } else {
      msg.className='status-msg status-err'; msg.textContent='❌ ' + (data.error || 'failed');
    }
  } catch(e) {
    msg.className='status-msg status-err'; msg.textContent='❌ Network error: ' + e.message;
  } finally { btn.disabled=false; btn.textContent='💾 Save Trading Settings'; }
}

loadStatus();
</script>
</body></html>"""
