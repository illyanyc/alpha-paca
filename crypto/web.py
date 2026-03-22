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

    regime = s.get("regime", {})
    micro = {}
    for pair, data in s.get("microstructure", {}).items():
        if hasattr(data, "__dict__"):
            micro[pair] = {k: getattr(data, k) for k in ("signal", "score", "bid_ask_imbalance", "spread_bps", "trade_flow_imbalance", "vpin")}
        elif isinstance(data, dict):
            micro[pair] = data

    onchain = s.get("onchain", {})
    if hasattr(onchain, "__dict__"):
        onchain = {"fear_greed_index": onchain.fear_greed_index, "fear_greed_label": onchain.fear_greed_label,
                    "signal": onchain.signal, "score": onchain.score, "btc_funding_rate": onchain.btc_funding_rate}

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
            "daily_pnl": round(portfolio.get("realized_pnl_today", 0), 2),
            "total_pnl": round(portfolio.get("total_realized_pnl", 0), 2),
            "total_trades": portfolio.get("total_trades", 0),
            "total_win_rate": round(portfolio.get("total_win_rate", 0), 1),
            "daily_trades": portfolio.get("daily_trades", 0),
            "daily_win_rate": round(portfolio.get("daily_win_rate", 0), 1),
        },
        "regime": regime if isinstance(regime, dict) else {},
        "microstructure": micro,
        "onchain": onchain if isinstance(onchain, dict) else {},
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
        "pnl_per_pair": s.get("pnl_summary", {}).get("per_pair", {}),
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


@app.get("/api/candles")
async def api_candles(request: Request, pair: str = "BTC/USD", granularity: str = "ONE_HOUR", limit: int = 200):
    if not _check_session(request):
        raise HTTPException(status_code=401)
    if not _state_ref:
        return JSONResponse([])
    from main import _exchange_ref
    if not _exchange_ref:
        return JSONResponse([])
    try:
        import asyncio
        bars = await asyncio.to_thread(_exchange_ref.get_candles, pair, granularity=granularity, limit=limit)
        return JSONResponse(bars[-limit:])
    except Exception as e:
        return JSONResponse({"error": str(e)[:100]}, status_code=500)


@app.get("/api/equity-curve")
async def api_equity_curve(request: Request):
    if not _check_session(request):
        raise HTTPException(status_code=401)
    if not _state_ref:
        return JSONResponse([])
    curve = _state_ref.get("equity_curve", [])
    return JSONResponse(curve[-2880:])


@app.get("/api/orderbook")
async def api_orderbook(request: Request, pair: str = "BTC/USD"):
    if not _check_session(request):
        raise HTTPException(status_code=401)
    if not _state_ref:
        return JSONResponse({"bids": [], "asks": []})
    micro_engine = _state_ref.get("micro_engine")
    if not micro_engine:
        return JSONResponse({"bids": [], "asks": []})
    tracker = micro_engine.get_tracker(pair)
    return JSONResponse({
        "bids": [(p, q) for p, q in tracker.bids[:15]],
        "asks": [(p, q) for p, q in tracker.asks[:15]],
        "imbalance": tracker.bid_ask_imbalance(),
    })


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
<title>Alpha-Paca Terminal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#000;--panel:#0a0a0a;--border:#1a1a1a;--amber:#FF9900;--green:#00C853;--red:#FF1744;
--cyan:#00BCD4;--dim:#555;--text:#ccc;--white:#eee;--blue:#2196F3}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;overflow-x:hidden;
-webkit-font-smoothing:antialiased}
a{color:var(--amber)}
.hdr{display:flex;align-items:center;justify-content:space-between;padding:6px 12px;background:#050505;border-bottom:1px solid var(--border)}
.hdr-left{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.hdr .title{color:var(--amber);font-weight:700;font-size:15px;letter-spacing:2px}
.hdr .meta{color:var(--dim);font-size:11px}
.hdr-right{display:flex;gap:8px;align-items:center}
.hdr-right a{font-size:11px;color:var(--dim);text-decoration:none;padding:4px 8px;border:1px solid var(--border);border-radius:4px}
.hdr-right a:hover{color:var(--amber);border-color:var(--amber)}
.regime-badge{display:inline-block;padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:1px}
.regime-trending_up{background:#00C85322;color:var(--green);border:1px solid var(--green)}
.regime-trending_down{background:#FF174422;color:var(--red);border:1px solid var(--red)}
.regime-mean_reverting{background:#2196F322;color:var(--blue);border:1px solid var(--blue)}
.regime-volatile{background:#FF990022;color:var(--amber);border:1px solid var(--amber)}
.live-ind{position:fixed;top:8px;right:12px;z-index:100;display:flex;align-items:center;gap:6px;
padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700;letter-spacing:1px}
.live-ind.ok{background:#00C85322;color:var(--green);border:1px solid #00C85344}
.live-ind.lost{background:#FF174422;color:var(--red);border:1px solid #FF174444}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 1.5s infinite}
.live-ind.lost .live-dot{background:var(--red)}
@keyframes pulse{50%{opacity:.3}}
.stat-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:2px;padding:4px}
.stat-grid2{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;padding:0 4px 4px}
.pnl{border:1px solid var(--border);background:var(--panel);padding:10px 12px;border-radius:4px}
.pnl-icon{font-size:14px;margin-right:4px;vertical-align:middle}
.pnl-title{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}
.pnl-val{font-size:20px;font-weight:700}
.pnl-sm{font-size:15px;font-weight:700}
.g{color:var(--green)}.r{color:var(--red)}.a{color:var(--amber)}.c{color:var(--cyan)}.w{color:var(--white)}
.section{border:1px solid var(--border);background:var(--panel);margin:4px;border-radius:4px;overflow:hidden}
.sec-hdr{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:#050505;border-bottom:1px solid var(--border)}
.sec-hdr span{color:var(--amber);font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px}
.sec-hdr .badge{display:inline-flex;align-items:center;justify-content:center;background:var(--amber);color:#000;
font-size:11px;font-weight:700;min-width:22px;height:22px;border-radius:11px;padding:0 6px;margin-left:6px}
.sec-body{padding:8px 12px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:5px 8px;border-bottom:1px solid var(--border)}
td{padding:5px 8px;border-bottom:1px solid #111}
tr:last-child td{border:none}
.rt{text-align:right}
.spark{letter-spacing:-1px;color:var(--cyan);font-size:13px}
.pair-icon{font-size:14px;margin-right:4px;vertical-align:middle}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}
.dot-running{background:var(--green);animation:pulse 1s infinite}
.dot-idle{background:var(--dim)}.dot-error{background:var(--red);animation:pulse .5s infinite}
.dot-healthy{background:var(--green)}.dot-standby{background:var(--cyan)}
.dot-healing{background:var(--amber);animation:pulse 1.5s infinite}
.tl{padding:3px 0;font-size:11px;border-bottom:1px solid #111;line-height:1.5}
.tl:last-child{border:none}
.tl-time{color:var(--dim);margin-right:6px}
.tl-orchestrator{color:var(--green)}.tl-order_executor{color:var(--red)}.tl-risk_validator{color:var(--amber)}
.tl-technical_analyst{color:var(--cyan)}.tl-fundamental_analyst{color:#a78bfa}.tl-news_scout{color:#ffd93d}
.pos-summary{display:flex;justify-content:flex-end;gap:16px;padding:6px 8px;border-top:1px solid var(--border);
font-size:12px;font-weight:700;background:#050505}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:0}
@media(max-width:700px){
.stat-grid{grid-template-columns:1fr 1fr}
.stat-grid2{grid-template-columns:1fr 1fr}
.two-col{grid-template-columns:1fr}
body{font-size:11px}
.pnl-val{font-size:17px}
.pnl-sm{font-size:13px}
.hide-mobile{display:none}
table{font-size:11px}
th,td{padding:4px 6px}
}
</style></head><body>

<div class="live-ind lost" id="conn"><span class="live-dot"></span><span id="conn-label">CONNECTING</span></div>

<div class="hdr">
<div class="hdr-left">
<span class="title">🦙 ALPHA-PACA</span>
<span id="regime-badge" class="regime-badge regime-volatile">VOLATILE</span>
<span class="meta">⏱ <span id="uptime">00:00:00</span></span>
<span class="meta" id="ts"></span>
<span id="exch-status" style="font-size:11px;color:var(--dim)"></span>
</div>
<div class="hdr-right">
<span id="fear-greed" style="font-size:11px;color:var(--dim)"></span>
<a href="/settings">⚙ SETTINGS</a>
<a href="/logout">🚪 LOGOUT</a>
</div>
</div>

<div class="stat-grid">
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">💎</span>NAV</div><div class="pnl-val w" id="nav">$0</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">💵</span>Cash</div><div class="pnl-val" id="cash">$0</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">📊</span>Exposure</div><div class="pnl-val" id="exposure">0%</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">📈</span>Day P&amp;L</div><div class="pnl-val" id="daily-pnl">$0</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">🏆</span>Total P&amp;L</div><div class="pnl-val" id="total-pnl">$0</div></div>
</div>
<div class="stat-grid2">
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">💰</span>Unrealized</div><div class="pnl-sm" id="unrealized">$0</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">📉</span>Drawdown</div><div class="pnl-sm" id="drawdown">0%</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">🎯</span>Win Rate</div><div class="pnl-sm" id="win-rate">0%</div></div>
<div class="pnl"><div class="pnl-title"><span class="pnl-icon">🔄</span>Trades</div><div class="pnl-sm w" id="trade-count">0</div></div>
</div>

<div class="section">
<div class="sec-hdr"><span>💰 Live Prices</span></div>
<div class="sec-body"><table>
<thead><tr><th>Pair</th><th class="rt">Mid</th><th class="rt">Spread</th><th class="rt hide-mobile">Micro</th><th class="hide-mobile">Chart</th></tr></thead>
<tbody id="prices-body"></tbody>
</table></div>
</div>

<div class="section">
<div class="sec-hdr"><span>📦 Positions</span><span id="pos-count" class="badge">0</span></div>
<div class="sec-body"><table>
<thead><tr><th>Asset</th><th>Side</th><th class="rt hide-mobile">Qty</th><th class="rt hide-mobile">Entry</th><th class="rt hide-mobile">Current</th><th class="rt">Value</th><th class="rt">P&amp;L</th><th class="rt">Alloc</th></tr></thead>
<tbody id="positions-body"></tbody>
</table></div>
<div id="pos-summary" class="pos-summary" style="display:none">
<span>Total Value: <span id="pos-total-val" class="w">$0</span></span>
<span>Unrealized: <span id="pos-total-pnl">$0</span></span>
</div>
</div>

<div class="two-col">
<div class="section">
<div class="sec-hdr"><span>📡 Signals</span></div>
<div class="sec-body"><table>
<thead><tr><th>Pair</th><th>Tech</th><th class="rt">Score</th><th>Fund</th><th class="rt">Score</th></tr></thead>
<tbody id="signals-body"></tbody>
</table>
<div id="news-sentiment" style="margin-top:6px;font-size:11px;color:var(--dim)"></div>
</div>
</div>

<div class="section">
<div class="sec-hdr"><span>🎯 Strategy Signals</span></div>
<div class="sec-body" id="strategy-body" style="font-size:11px"></div>
</div>
</div>

<div class="section">
<div class="sec-hdr"><span>📋 Recent Trades</span></div>
<div class="sec-body"><table>
<thead><tr><th>Time</th><th>Side</th><th>Pair</th><th class="rt">Price</th><th class="rt">P&amp;L</th></tr></thead>
<tbody id="trades-body"></tbody>
</table><div id="no-trades" style="color:var(--dim);text-align:center;padding:8px;font-size:11px">No trades yet</div></div>
</div>

<div class="two-col">
<div class="section">
<div class="sec-hdr"><span>🤖 Agents</span></div>
<div class="sec-body" id="agents-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:4px"></div>
</div>

<div class="section" id="healing-section" style="display:none">
<div class="sec-hdr"><span>🩺 Self-Healing</span></div>
<div class="sec-body"><table>
<thead><tr><th>Time</th><th>Agent</th><th>Status</th><th>Event</th></tr></thead>
<tbody id="healing-body"></tbody>
</table></div>
</div>
</div>

<div class="section">
<div class="sec-hdr"><span>🧠 Agent Thinking</span></div>
<div class="sec-body" id="thinking-log" style="max-height:300px;overflow-y:auto;font-size:11px"></div>
</div>

<script>
const $=id=>document.getElementById(id);
const SPARK='▁▂▃▄▅▆▇█';
const IC={'BTC/USD':'₿','ETH/USD':'Ξ','SOL/USD':'◎','DOGE/USD':'Ð','LINK/USD':'⬡','ALGO/USD':'Ⱥ','AVAX/USD':'🔺','MATIC/USD':'⬟','ADA/USD':'₳'};
function icon(pair){return IC[pair]||'●';}
function sparkline(arr){if(!arr||arr.length<2)return'—';const v=arr.slice(-30);const mn=Math.min(...v),mx=Math.max(...v),rng=mx-mn||1;return v.map(x=>SPARK[Math.min(Math.floor((x-mn)/rng*7),7)]).join('');}
function fmt(n){if(n>=1e3)return'$'+n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});if(n>=1)return'$'+n.toFixed(4);return'$'+n.toFixed(6);}
function pc(v){return v>0?'g':v<0?'r':'';}
function sc(s){s=(s||'').toLowerCase();return s.includes('buy')?'g':s.includes('sell')?'r':'a';}
function ut(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60),ss=s%60;return[h,m,ss].map(x=>String(x).padStart(2,'0')).join(':');}
const AI={news_scout:'📰',technical_analyst:'📈',fundamental_analyst:'🔬',orchestrator:'🧠',risk_validator:'🛡️',order_executor:'⚡'};

function render(d){
if(!d||!d.portfolio)return;
const p=d.portfolio;
const exch=d.exchange||{};
const es=$('exch-status');
if(exch.status==='connected'){es.innerHTML='<span style="color:var(--green)">✅ Coinbase</span>';}
else if(exch.status==='unauthorized'){es.innerHTML='<span style="color:var(--red)">❌ CB Auth Fail</span>';}
else if(exch.status==='market_only'){es.innerHTML='<span style="color:var(--amber)">📊 Market Only</span>';}
else{es.innerHTML='<span style="color:var(--dim)">⏳ Checking</span>';}

$('uptime').textContent=ut(d.uptime);
$('ts').textContent=(d.ts||'').replace('T',' ').slice(0,19)+' UTC';

const rg=d.regime||{};
const rb=$('regime-badge');
const rl=rg.label||'UNKNOWN';
rb.textContent=rl+(rg.confidence?` ${(rg.confidence*100).toFixed(0)}%`:'');
rb.className='regime-badge regime-'+(rg.regime||'volatile');

const oc=d.onchain||{};
const fg=$('fear-greed');
if(oc.fear_greed_index){
const fv=oc.fear_greed_index;
const fc=fv<25?'var(--red)':fv<45?'var(--amber)':fv<55?'var(--dim)':fv<75?'var(--green)':'var(--red)';
fg.innerHTML=`😱 F&G: <span style="color:${fc};font-weight:700">${fv}</span> ${oc.fear_greed_label||''}`;
}

$('nav').textContent='$'+p.nav.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
$('cash').textContent='$'+p.cash.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
$('cash').className='pnl-val '+(p.cash>0?'g':'');
const ee=$('exposure');ee.textContent=p.exposure_pct.toFixed(1)+'%';
ee.className='pnl-val '+(p.exposure_pct<70?'g':p.exposure_pct<90?'a':'r');
const dp=$('daily-pnl');dp.textContent='$'+(p.daily_pnl>=0?'+':'')+p.daily_pnl.toFixed(2);dp.className='pnl-val '+pc(p.daily_pnl);
const tp=$('total-pnl');tp.textContent='$'+(p.total_pnl>=0?'+':'')+p.total_pnl.toFixed(2);tp.className='pnl-val '+pc(p.total_pnl);
const ue=$('unrealized');ue.textContent='$'+(p.unrealized_pnl>=0?'+':'')+p.unrealized_pnl.toFixed(2);ue.className='pnl-sm '+pc(p.unrealized_pnl);
const dd=$('drawdown');dd.textContent=p.drawdown_pct.toFixed(1)+'%';dd.className='pnl-sm '+(p.drawdown_pct<5?'g':p.drawdown_pct<10?'a':'r');
const wr=$('win-rate');const wrv=p.total_win_rate||0;wr.textContent=wrv.toFixed(0)+'%';wr.className='pnl-sm '+(wrv>=50?'g':wrv>0?'r':'');
$('trade-count').textContent=(p.total_trades||0)+' ('+(p.daily_trades||0)+' today)';

const micro=d.microstructure||{};
let ph='';
Object.keys(d.prices||{}).sort().forEach(pair=>{
const px=d.prices[pair];const m=micro[pair]||{};
const spc=px.spread_bps<20?'g':px.spread_bps<50?'a':'r';
const mSig=m.signal||'—';const mCls=sc(mSig);
ph+=`<tr><td><span class="pair-icon">${icon(pair)}</span><b>${pair}</b></td><td class="rt">${fmt(px.mid)}</td>`+
`<td class="rt ${spc}">${px.spread_bps.toFixed(1)}bps</td>`+
`<td class="rt hide-mobile ${mCls}">${mSig}</td>`+
`<td class="spark hide-mobile">${sparkline(px.history)}</td></tr>`;
});
$('prices-body').innerHTML=ph||'<tr><td colspan="5" style="color:var(--dim);text-align:center">Loading...</td></tr>';

const nav=p.nav||1;
let posH='';let totVal=0;let totPnl=0;
const posArr=d.positions||[];
posArr.forEach(pos=>{
const alloc=nav>0?((pos.value||0)/nav*100).toFixed(1):'0';
totVal+=pos.value||0;totPnl+=pos.pnl||0;
posH+=`<tr><td><span class="pair-icon">${icon(pos.pair)}</span><b>${pos.pair}</b></td>`+
`<td style="color:${pos.side==='short'?'var(--red)':'var(--green)'};">${(pos.side||'long').toUpperCase()}</td>`+
`<td class="rt hide-mobile">${pos.qty.toFixed(6)}</td><td class="rt hide-mobile">${fmt(pos.entry)}</td><td class="rt hide-mobile">${fmt(pos.current)}</td>`+
`<td class="rt">${fmt(pos.value||0)}</td>`+
`<td class="rt ${pc(pos.pnl)}">$${pos.pnl>=0?'+':''}${pos.pnl.toFixed(2)} (${pos.pnl_pct>=0?'+':''}${pos.pnl_pct.toFixed(1)}%)</td>`+
`<td class="rt a">${alloc}%</td></tr>`;
});
$('positions-body').innerHTML=posH||'<tr><td colspan="8" style="color:var(--dim);text-align:center">No positions</td></tr>';
$('pos-count').textContent=posArr.length;
const ps=$('pos-summary');
if(posArr.length>0){ps.style.display='flex';$('pos-total-val').textContent=fmt(totVal);
const ptp=$('pos-total-pnl');ptp.textContent='$'+(totPnl>=0?'+':'')+totPnl.toFixed(2);ptp.className=pc(totPnl);}
else{ps.style.display='none';}

const ap=[...new Set([...Object.keys(d.tech_signals||{}),...Object.keys(d.fund_signals||{})])].sort();
let sh='';
ap.forEach(pair=>{
const t=(d.tech_signals||{})[pair]||{};const f=(d.fund_signals||{})[pair]||{};
sh+=`<tr><td><span class="pair-icon">${icon(pair)}</span><b>${pair}</b></td><td class="${sc(t.signal)}">${t.signal||'—'}</td><td class="rt">${(t.score||0).toFixed(2)}</td>`+
`<td class="${sc(f.signal)}">${f.signal||'—'}</td><td class="rt">${(f.score||0).toFixed(2)}</td></tr>`;
});
$('signals-body').innerHTML=sh||'<tr><td colspan="5" style="color:var(--dim);text-align:center">Loading...</td></tr>';
const ns=d.news||{};
$('news-sentiment').innerHTML=`📰 News: <span class="${sc(ns.sentiment)}">${ns.sentiment||'—'}</span> (${(ns.score||0).toFixed(2)})`;

const trades=d.recent_trades||[];
if(trades.length>0){
$('no-trades').style.display='none';
let th='';
trades.slice().reverse().slice(0,12).forEach(t=>{
th+=`<tr><td style="white-space:nowrap;color:var(--dim)">${t.time}</td>`+
`<td style="color:${t.side.toLowerCase()==='buy'?'var(--green)':'var(--red)'};font-weight:700">${t.side}</td>`+
`<td><span class="pair-icon">${icon(t.pair)}</span>${t.pair}</td><td class="rt">${fmt(t.price)}</td>`+
`<td class="rt ${pc(t.pnl)}">$${t.pnl>=0?'+':''}${t.pnl.toFixed(2)}</td></tr>`;
});
$('trades-body').innerHTML=th;
}else{$('no-trades').style.display='';$('trades-body').innerHTML='';}

let ah='';
Object.entries(d.agents||{}).forEach(([n,s])=>{
const ic=AI[n]||'🤖';
ah+=`<div style="display:flex;align-items:center;padding:5px 8px;background:#050505;border-radius:4px;font-size:12px"><span class="dot dot-${s}"></span>${ic} ${n.replace(/_/g,' ')}</div>`;
});
$('agents-grid').innerHTML=ah;

const strats=d.strategy_signals||{};
const sb=$('strategy-body');
const sp=Object.keys(strats);
if(sp.length>0){
let ss='<div style="display:flex;flex-wrap:wrap;gap:6px">';
sp.forEach(pair=>{
const s=strats[pair];
const buys=(s.buy||[]).map(n=>'<span class="g">▲'+n+'</span>').join(' ');
const sells=(s.sell||[]).map(n=>'<span class="r">▼'+n+'</span>').join(' ');
ss+=`<div style="background:#050505;padding:4px 8px;border-radius:4px;border:1px solid var(--border)"><span class="pair-icon">${icon(pair)}</span><b>${pair}</b> ${buys} ${sells}</div>`;
});
const bt=d.backtest||{};
if(bt.aggregate){
ss+='</div><div style="margin-top:8px;color:var(--dim);font-size:10px">';
bt.aggregate.forEach(a=>{
const col=a.sharpe>0.5?'var(--green)':a.sharpe>0?'var(--amber)':'var(--red)';
ss+=`<span style="color:${col}">${a.name}(S=${a.sharpe.toFixed(1)},W=${(a.win_rate*100).toFixed(0)}%,w=${(a.weight*100).toFixed(0)}%)</span> `;
});
ss+='</div>';
}
sb.innerHTML=ss;
}else{sb.innerHTML='<span style="color:var(--dim)">Computing...</span>';}

const al=d.agent_log||[];
const td=$('thinking-log');
if(al.length>0){
let tl='';
al.slice(-30).forEach(e=>{
const ts=(e.ts||'').slice(11,19);
const ic=AI[e.agent]||'🤖';
tl+=`<div class="tl"><span class="tl-time">${ts}</span><span class="tl-${e.agent}" style="font-weight:700">${ic} ${e.agent.replace(/_/g,' ')}</span> ${e.step||''}</div>`;
});
td.innerHTML=tl;
td.scrollTop=td.scrollHeight;
}else{td.innerHTML='<div style="color:var(--dim);text-align:center;padding:10px">Waiting for agent cycle...</div>';}

const hl=d.healing||[];const hs=$('healing-section');
if(hl.length>0){
hs.style.display='';
const OI={healed:'✅',retrying:'🔄',circuit_open:'🔶',skipped:'⏭️',retry_failed:'❌'};
const SC={critical:'r',warning:'a',transient:'c',info:'g'};
let hh='';
hl.slice().reverse().slice(0,6).forEach(h=>{
const ts=(h.timestamp||'').slice(11,19);
hh+=`<tr><td style="white-space:nowrap;color:var(--dim)">${ts}</td><td>${h.agent}</td><td>${OI[h.outcome]||''}</td><td class="${SC[h.severity]||''}">${h.message}</td></tr>`;
});
$('healing-body').innerHTML=hh;
}else{hs.style.display='none';}
}

const ce=$('conn'),cl=$('conn-label');
let ws,rt;
function connect(){
const pr=location.protocol==='https:'?'wss:':'ws:';
ws=new WebSocket(pr+'//'+location.host+'/ws');
ws.onopen=()=>{cl.textContent='LIVE';ce.className='live-ind ok';};
ws.onclose=()=>{cl.textContent='RECONNECTING';ce.className='live-ind lost';rt=setTimeout(connect,3000);};
ws.onerror=()=>ws.close();
ws.onmessage=e=>{try{render(JSON.parse(e.data));}catch(err){console.error(err);}};
}
connect();
setInterval(async()=>{
if(ws&&ws.readyState===WebSocket.OPEN)return;
try{const r=await fetch('/api/state',{credentials:'same-origin'});if(r.ok)render(await r.json());}catch(e){}
},5000);
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
    } else if (exch.status === 'market_only') {
      cs.className = 'current-status cs-fail'; cs.innerHTML = '📊 Market data active — need CDP PEM keys for trading. Create at <a href="https://portal.cdp.coinbase.com/projects/api-keys" target="_blank" style="color:#4fc3f7">portal.cdp.coinbase.com</a> (ECDSA/ES256)';
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
