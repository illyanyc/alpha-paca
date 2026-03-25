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
        bot_id = p.get("bot_id", "swing")
        positions.append({
            "pair": pair, "side": side, "qty": qty, "entry": entry, "current": current,
            "pnl": round(pnl, 4), "pnl_pct": round(pnl_pct, 2), "value": round(mv, 2),
            "bot_id": bot_id,
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
            "bot_id": t.get("bot_id", "?"),
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
            "max_risk_per_trade_pct": settings.crypto.max_risk_per_trade_pct,
            "max_leverage": settings.crypto.max_leverage,
            "min_conviction": settings.crypto.min_conviction,
            "daily_loss_halt_pct": settings.crypto.daily_loss_halt_pct,
            "max_drawdown_pct": settings.crypto.max_drawdown_pct,
            "max_concurrent_per_bot": settings.crypto.max_concurrent_per_bot,
            "max_concurrent_total": settings.crypto.max_concurrent_total,
            "day_min_rr_ratio": settings.crypto.day_min_rr_ratio,
            "swing_min_rr_ratio": settings.crypto.swing_min_rr_ratio,
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
            "buying_power": round(portfolio.get("buying_power", 0), 2),
            "exposure_pct": round(portfolio.get("total_exposure_pct", 0), 1),
            "unrealized_pnl": round(portfolio.get("unrealized_pnl", 0), 2),
            "drawdown_pct": round(portfolio.get("drawdown_pct", 0), 1),
            "daily_pnl": round(portfolio.get("realized_pnl_today", 0), 2),
            "total_pnl": round(portfolio.get("total_realized_pnl", 0) + portfolio.get("unrealized_pnl", 0), 2),
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


@app.post("/api/rebalance")
async def api_rebalance(request: Request):
    """On-demand rebalance: pull fresh news/technicals/on-chain, score all pairs, execute qualifying trades."""
    if not _check_session(request):
        raise HTTPException(status_code=401)
    try:
        from main import run_rebalance
        result = await run_rebalance()
        status_code = 200 if result.get("status") == "ok" else 500
        return JSONResponse(result, status_code=status_code)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)[:200]}, status_code=500)


@app.get("/api/learnings")
async def api_learnings(request: Request):
    """View and manage prompt optimizer learnings stored in Redis."""
    if not _check_session(request):
        raise HTTPException(status_code=401)
    try:
        from agents.prompt_optimizer import get_current_learnings
        import redis.asyncio as aioredis
        from config import get_settings
        settings = get_settings()
        r = aioredis.from_url(settings.database.redis_url, decode_responses=True)
        learnings = await get_current_learnings(r)
        await r.aclose()
        return JSONResponse(learnings)
    except Exception as e:
        return JSONResponse({"error": str(e)[:100]}, status_code=500)


@app.delete("/api/learnings/{bot_id}")
async def api_delete_learnings(request: Request, bot_id: str):
    """Clear all learnings for a specific bot."""
    if not _check_session(request):
        raise HTTPException(status_code=401)
    if bot_id not in ("swing", "day"):
        raise HTTPException(status_code=400, detail="bot_id must be 'swing' or 'day'")
    try:
        import redis.asyncio as aioredis
        from config import get_settings
        settings = get_settings()
        r = aioredis.from_url(settings.database.redis_url, decode_responses=True)
        await r.delete(f"crypto:learnings:{bot_id}")
        await r.aclose()
        return JSONResponse({"status": "ok", "bot_id": bot_id})
    except Exception as e:
        return JSONResponse({"error": str(e)[:100]}, status_code=500)


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


def _to_float(v) -> float | None:
    """Safely coerce any numeric (numpy, Decimal, etc.) to a plain float."""
    if v is None:
        return None
    try:
        f = float(v)
        import math
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_ind(d: dict) -> dict:
    """Convert indicators dict to JSON-safe format."""
    out: dict = {}
    for k, v in d.items():
        if v is None:
            continue
        fv = _to_float(v)
        if fv is not None:
            out[k] = round(fv, 6)
        elif isinstance(v, (bool,)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v
        elif isinstance(v, list):
            safe_list = []
            for item in (v[-60:] if len(v) > 60 else v):
                fi = _to_float(item)
                safe_list.append(round(fi, 6) if fi is not None else None)
            out[k] = safe_list
        else:
            try:
                out[k] = str(v)
            except Exception:
                pass
    return out


def _safe_candles(bars: list) -> list:
    """Coerce candle list into plain JSON-safe dicts."""
    out = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        out.append({
            k: (_to_float(v) if k != "timestamp" and k != "start" and k != "time" else v)
            for k, v in b.items()
        })
    return out


def _compute_series(bars: list) -> dict:
    """Compute indicator time-series from raw candle bars for chart rendering."""
    if len(bars) < 30:
        return {}
    try:
        closes = [float(b.get("close", 0)) for b in bars]

        def _ema(data, period):
            if len(data) < period:
                return []
            k = 2 / (period + 1)
            out = [sum(data[:period]) / period]
            for v in data[period:]:
                out.append(v * k + out[-1] * (1 - k))
            return out

        series: dict = {}
        ema8 = _ema(closes, 8)
        ema21 = _ema(closes, 21)
        if ema8:
            series["ema_8_series"] = [round(v, 4) for v in ema8]
        if ema21:
            series["ema_21_series"] = [round(v, 4) for v in ema21]

        if len(closes) > 6:
            deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
            gains = [max(d, 0) for d in deltas]
            losses = [-min(d, 0) for d in deltas]
            p = 5
            if len(deltas) >= p + 1:
                ag = sum(gains[:p]) / p
                al_ = sum(losses[:p]) / p
                rsi_out = []
                for i in range(p, len(deltas)):
                    ag = (ag * (p - 1) + gains[i]) / p
                    al_ = (al_ * (p - 1) + losses[i]) / p
                    rs = ag / al_ if al_ > 0 else 100
                    rsi_out.append(round(100 - 100 / (1 + rs), 2))
                series["rsi_series"] = rsi_out

        ema_f = _ema(closes, 8)
        ema_s = _ema(closes, 17)
        if ema_f and ema_s:
            off = 17 - 8
            ml = [ema_f[off + i] - ema_s[i] for i in range(len(ema_s))]
            sl = _ema(ml, 9)
            if sl:
                off2 = 9 - 1
                hl = [round(ml[off2 + i] - sl[i], 6) for i in range(len(sl))]
                series["macd_series"] = [round(v, 6) for v in ml]
                series["signal_series"] = [round(v, 6) for v in sl]
                series["hist_series"] = hl

        return series
    except Exception:
        return {}


@app.get("/api/pair-detail")
async def api_pair_detail(request: Request, pair: str = "BTC/USD"):
    """Full detail for a single pair: candles, indicators, composite scores, trades."""
    if not _check_session(request):
        raise HTTPException(status_code=401)
    if not _state_ref:
        return JSONResponse({"pair": pair, "candles_1h": [], "candles_4h": [],
                             "indicators_4h": {}, "indicators_daily": {},
                             "composite_score": 0, "recent_trades": [],
                             "db_trades": [], "pnl": {}})

    s = _state_ref
    candles_4h = s.get("candles_4h", {}).get(pair, [])
    ind_4h = s.get("indicators_4h", {}).get(pair, {})
    ind_daily = s.get("indicators_daily", {}).get(pair, {})
    composite = _to_float(s.get("composite_scores", {}).get(pair, 0)) or 0

    pair_trades = [
        t for t in s.get("recent_trades", [])
        if t.get("pair") == pair
    ]

    candles_1h: list = []
    try:
        from main import _exchange_ref
        if _exchange_ref:
            import asyncio
            candles_1h = await asyncio.to_thread(
                _exchange_ref.get_candles, pair, granularity="ONE_HOUR", limit=200
            )
    except Exception:
        pass

    db_trades: list = []
    try:
        from db.engine import async_session_factory
        from db.models import CryptoTrade
        from sqlalchemy import select
        async with async_session_factory() as sess:
            q = (
                select(CryptoTrade)
                .where(CryptoTrade.pair == pair)
                .order_by(CryptoTrade.opened_at.desc())
                .limit(100)
            )
            rows = (await sess.execute(q)).scalars().all()
            for r in reversed(rows):
                db_trades.append({
                    "id": r.id,
                    "side": r.side,
                    "qty": float(r.qty or 0),
                    "entry_price": float(r.entry_price or 0),
                    "exit_price": float(r.exit_price or 0) if r.exit_price else None,
                    "pnl": float(r.pnl or 0) if r.pnl else None,
                    "pnl_pct": float(r.pnl_pct) if r.pnl_pct else None,
                    "target_price": float(r.target_price) if r.target_price else None,
                    "stop_price": float(r.stop_price) if r.stop_price else None,
                    "status": r.status,
                    "bot_id": r.bot_id,
                    "reasoning": (r.reasoning or "")[:80],
                    "opened_at": str(r.opened_at)[:19] if r.opened_at else None,
                    "closed_at": str(r.closed_at)[:19] if r.closed_at else None,
                })
    except Exception:
        pass

    pnl_per_pair = s.get("pnl_summary", {}).get("per_pair", {}).get(pair, {})
    safe_pnl = {}
    for k, v in pnl_per_pair.items():
        fv = _to_float(v)
        safe_pnl[k] = fv if fv is not None else v

    ind_safe = _safe_ind(ind_4h)

    candles_for_series = candles_1h if candles_1h else candles_4h
    chart_series = _compute_series(candles_for_series)
    ind_safe.update(chart_series)

    return JSONResponse({
        "pair": pair,
        "candles_1h": _safe_candles(candles_1h[-200:]),
        "candles_4h": _safe_candles(candles_4h[-120:]),
        "indicators_4h": ind_safe,
        "indicators_daily": _safe_ind(ind_daily),
        "composite_score": composite,
        "recent_trades": pair_trades,
        "db_trades": db_trades,
        "pnl": safe_pnl,
    })


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
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{--bg:#000;--panel:#0a0a0a;--border:#1a1a1a;--amber:#FF9900;--green:#00C853;--red:#FF1744;
--cyan:#00BCD4;--dim:#555;--text:#ccc;--white:#eee;--blue:#2196F3}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;overflow-x:hidden;
-webkit-font-smoothing:antialiased}
a{color:var(--amber)}
.hdr{display:flex;align-items:center;justify-content:space-between;padding:6px 12px;background:#050505;
border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50}
.hdr-left{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0;overflow:hidden}
.hdr .title{color:var(--amber);font-weight:700;font-size:15px;letter-spacing:2px;white-space:nowrap}
.hdr .meta{color:var(--dim);font-size:11px;white-space:nowrap}
.hdr-right{display:flex;gap:6px;align-items:center;flex-shrink:0}
.hdr-right a{font-size:11px;color:var(--dim);text-decoration:none;padding:4px 8px;border:1px solid var(--border);border-radius:4px;white-space:nowrap}
.hdr-right a:hover{color:var(--amber);border-color:var(--amber)}
.rebalance-link{color:var(--amber)!important;border-color:var(--amber)!important;font-weight:700}
.rebalance-link:hover{background:var(--amber)!important;color:#000!important}
.rebalance-link.running{opacity:.6;pointer-events:none;animation:pulse 1s infinite}
.rebalance-toast{position:fixed;top:48px;right:12px;z-index:300;background:#131a2b;border:1px solid var(--border);
border-radius:6px;padding:10px 14px;font-size:11px;max-width:420px;box-shadow:0 8px 32px rgba(0,0,0,.6);
transition:opacity .3s;opacity:0;pointer-events:none}
.rebalance-toast.show{opacity:1;pointer-events:auto}
.rebalance-toast .rt-title{font-weight:700;color:var(--amber);margin-bottom:6px;font-size:12px}
.rebalance-toast .rt-row{display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #1a1a1a}
.rebalance-toast .rt-row:last-child{border:none}
.rebalance-toast .rt-close{position:absolute;top:4px;right:8px;cursor:pointer;color:var(--dim);font-size:14px}
.conn-badge{display:flex;align-items:center;gap:5px;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1px;white-space:nowrap}
.conn-badge.ok{background:#00C85322;color:var(--green);border:1px solid #00C85344}
.conn-badge.lost{background:#FF174422;color:var(--red);border:1px solid #FF174444}
.conn-dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 1.5s infinite}
.conn-badge.lost .conn-dot{background:var(--red);animation:pulse .5s infinite}
.regime-badge{display:inline-block;padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:1px}
.regime-trending_up{background:#00C85322;color:var(--green);border:1px solid var(--green)}
.regime-trending_down{background:#FF174422;color:var(--red);border:1px solid var(--red)}
.regime-mean_reverting{background:#2196F322;color:var(--blue);border:1px solid var(--blue)}
.regime-volatile{background:#FF990022;color:var(--amber);border:1px solid var(--amber)}
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
.clickable-row{cursor:pointer;transition:background .15s}
.clickable-row:hover{background:#111!important}
.chart-btn{background:none;border:1px solid var(--border);color:var(--amber);font-size:14px;padding:3px 8px;
border-radius:4px;cursor:pointer;font-family:inherit;line-height:1;transition:.15s}
.chart-btn:hover{background:var(--amber);color:#000;border-color:var(--amber)}
/* ── Pair Detail Modal ── */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:200;overflow-y:auto;-webkit-overflow-scrolling:touch}
.modal-overlay.open{display:block}
.modal{max-width:1100px;margin:20px auto;background:#080808;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.modal-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#050505;border-bottom:1px solid var(--border)}
.modal-hdr h2{font-size:16px;color:var(--amber);letter-spacing:1px;display:flex;align-items:center;gap:8px}
.modal-close{background:none;border:1px solid var(--border);color:var(--dim);font-size:18px;cursor:pointer;
padding:2px 10px;border-radius:4px;font-family:inherit}
.modal-close:hover{color:var(--red);border-color:var(--red)}
.modal-body{padding:12px}
.modal-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin-bottom:12px}
.modal-stat{background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:8px 10px}
.modal-stat-label{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.modal-stat-val{font-size:16px;font-weight:700;margin-top:2px}
.chart-container{width:100%;border:1px solid var(--border);border-radius:4px;overflow:hidden;margin-bottom:8px;background:#050505}
.chart-tabs{display:flex;gap:0;margin-bottom:8px}
.chart-tab{padding:6px 14px;background:var(--panel);border:1px solid var(--border);color:var(--dim);cursor:pointer;
font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;font-family:inherit}
.chart-tab:first-child{border-radius:4px 0 0 4px}
.chart-tab:last-child{border-radius:0 4px 4px 0}
.chart-tab.active{background:var(--amber);color:#000;border-color:var(--amber)}
.ind-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px;margin:10px 0}
.ind-item{background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:6px 8px}
.ind-item-label{font-size:9px;color:var(--dim);text-transform:uppercase}
.ind-item-val{font-size:13px;font-weight:700;margin-top:1px}
.trade-list{max-height:250px;overflow-y:auto}
.trade-list table{font-size:11px}
.modal-section-title{font-size:11px;font-weight:700;color:var(--amber);text-transform:uppercase;letter-spacing:1px;
margin:12px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--border)}
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
.modal{margin:8px}
.hdr-left .meta:nth-child(n+4){display:none}
}
</style></head><body>

<div class="hdr">
<div class="hdr-left">
<span class="title">🦙 ALPHA-PACA</span>
<div class="conn-badge lost" id="conn"><span class="conn-dot"></span><span id="conn-label">CONNECTING</span></div>
<span id="regime-badge" class="regime-badge regime-volatile">VOLATILE</span>
<span class="meta">⏱ <span id="uptime">00:00:00</span></span>
<span class="meta" id="ts"></span>
<span id="exch-status" style="font-size:11px;color:var(--dim)"></span>
</div>
<div class="hdr-right">
<span id="fear-greed" style="font-size:11px;color:var(--dim)"></span>
<a href="#" id="rebalance-btn" onclick="doRebalance(event)" class="rebalance-link">⚡ REBALANCE</a>
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
<thead><tr><th>Pair</th><th class="rt">Mid</th><th class="rt">Spread</th><th class="rt hide-mobile">Micro</th><th style="width:120px">Trend</th><th></th></tr></thead>
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

<!-- ── Rebalance Toast ── -->
<div class="rebalance-toast" id="rebalance-toast">
<span class="rt-close" onclick="$('rebalance-toast').classList.remove('show')">&times;</span>
<div class="rt-title" id="rt-title">Rebalancing...</div>
<div id="rt-body"></div>
</div>

<!-- ── Pair Detail Modal ── -->
<div class="modal-overlay" id="pair-modal">
<div class="modal">
<div class="modal-hdr">
<h2 id="modal-title">Loading...</h2>
<button class="modal-close" id="modal-close">&times;</button>
</div>
<div class="modal-body">
<div class="modal-stats" id="modal-stats"></div>
<div class="chart-tabs" id="chart-tabs">
<div class="chart-tab active" data-tf="1h">1H</div>
<div class="chart-tab" data-tf="4h">4H</div>
</div>
<div class="chart-container" id="price-chart" style="height:340px"></div>
<div class="chart-container" id="macd-chart" style="height:120px"></div>
<div class="chart-container" id="rsi-chart" style="height:100px"></div>
<div class="chart-container" id="vol-chart" style="height:80px"></div>
<div class="modal-section-title">📊 Technical Indicators (4H)</div>
<div class="ind-grid" id="ind-grid"></div>
<div class="modal-section-title">📋 Trade History &amp; Entry/Exit</div>
<div class="trade-list" id="modal-trades"></div>
<div class="modal-section-title">💰 Cumulative P&amp;L</div>
<div class="chart-container" id="pnl-chart" style="height:140px"></div>
</div>
</div>
</div>

<script>
const $=id=>document.getElementById(id);
const IC={'BTC/USD':'₿','ETH/USD':'Ξ','SOL/USD':'◎','DOGE/USD':'Ð','LINK/USD':'⬡','ALGO/USD':'Ⱥ','AVAX/USD':'🔺','MATIC/USD':'⬟','ADA/USD':'₳','XRP/USD':'✕'};
function icon(pair){return IC[pair]||'●';}
function svgSparkline(arr){
  if(!arr||arr.length<3)return'<span style="color:var(--dim)">—</span>';
  const v=arr.slice(-40);const w=120,h=32,pad=1;
  const mn=Math.min(...v),mx=Math.max(...v),rng=mx-mn||1;
  const pts=v.map((y,i)=>{const x=pad+i/(v.length-1)*(w-2*pad);const cy=pad+(1-(y-mn)/rng)*(h-2*pad);return`${x.toFixed(1)},${cy.toFixed(1)}`;});
  const up=v[v.length-1]>=v[0];
  const col=up?'var(--green)':'var(--red)';
  const fill=up?'rgba(0,200,83,0.12)':'rgba(255,23,68,0.12)';
  const polyFill=pts.join(' ')+` ${w-pad},${h} ${pad},${h}`;
  return`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block">`+
    `<polygon points="${polyFill}" fill="${fill}"/>`+
    `<polyline points="${pts.join(' ')}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linejoin="round"/>`+
    `<circle cx="${pts[pts.length-1].split(',')[0]}" cy="${pts[pts.length-1].split(',')[1]}" r="2" fill="${col}"/>`+
    `</svg>`;
}
function fmt(n){if(n>=1e3)return'$'+n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});if(n>=1)return'$'+n.toFixed(4);return'$'+n.toFixed(6);}
function pc(v){return v>0?'g':v<0?'r':'';}
function sc(s){s=(s||'').toLowerCase();return s.includes('buy')?'g':s.includes('sell')?'r':'a';}
function ut(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60),ss=s%60;return[h,m,ss].map(x=>String(x).padStart(2,'0')).join(':');}
const AI={news_scout:'📰',momentum:'📈',fundamental_analyst:'🔬',orchestrator:'🧠',risk_validator:'🛡️',order_executor:'⚡',swing_sniper:'🎯'};

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
ph+=`<tr class="clickable-row" onclick="openPairDetail('${pair}')"><td><span class="pair-icon">${icon(pair)}</span><b>${pair}</b></td><td class="rt">${fmt(px.mid)}</td>`+
`<td class="rt ${spc}">${px.spread_bps.toFixed(1)}bps</td>`+
`<td class="rt hide-mobile ${mCls}">${mSig}</td>`+
`<td>${svgSparkline(px.history)}</td>`+
`<td><button class="chart-btn" onclick="event.stopPropagation();openPairDetail('${pair}')">📈</button></td></tr>`;
});
$('prices-body').innerHTML=ph||'<tr><td colspan="6" style="color:var(--dim);text-align:center">Loading...</td></tr>';

const nav=p.nav||1;
let posH='';let totVal=0;let totPnl=0;
const posArr=d.positions||[];
posArr.forEach(pos=>{
const alloc=nav>0?((pos.value||0)/nav*100).toFixed(1):'0';
totVal+=pos.value||0;totPnl+=pos.pnl||0;
posH+=`<tr class="clickable-row" onclick="openPairDetail('${pos.pair}')"><td><span class="pair-icon">${icon(pos.pair)}</span><b>${pos.pair}</b></td>`+
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
sh+=`<tr class="clickable-row" onclick="openPairDetail('${pair}')"><td><span class="pair-icon">${icon(pair)}</span><b>${pair}</b></td><td class="${sc(t.signal)}">${t.signal||'—'}</td><td class="rt">${(t.score||0).toFixed(2)}</td>`+
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
th+=`<tr class="clickable-row" onclick="openPairDetail('${t.pair}')"><td style="white-space:nowrap;color:var(--dim)">${t.time}</td>`+
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
ss+=`<div style="background:#050505;padding:4px 8px;border-radius:4px;border:1px solid var(--border);cursor:pointer" onclick="openPairDetail('${pair}')"><span class="pair-icon">${icon(pair)}</span><b>${pair}</b> ${buys} ${sells}</div>`;
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

/* ── WebSocket ── */
const ce=$('conn'),cl=$('conn-label');
let ws,rt;
function connect(){
const pr=location.protocol==='https:'?'wss:':'ws:';
ws=new WebSocket(pr+'//'+location.host+'/ws');
ws.onopen=()=>{cl.textContent='LIVE';ce.className='conn-badge ok';};
ws.onclose=()=>{cl.textContent='RECONNECTING';ce.className='conn-badge lost';rt=setTimeout(connect,3000);};
ws.onerror=()=>ws.close();
ws.onmessage=e=>{try{render(JSON.parse(e.data));}catch(err){console.error(err);}};
}
connect();
setInterval(async()=>{
if(ws&&ws.readyState===WebSocket.OPEN)return;
try{const r=await fetch('/api/state',{credentials:'same-origin'});if(r.ok)render(await r.json());}catch(e){}
},5000);

/* ── Pair Detail Modal ── */
let _charts={};
let _modalData=null;
let _activeTF='1h';

$('modal-close').onclick=closePairDetail;
$('pair-modal').onclick=e=>{if(e.target===$('pair-modal'))closePairDetail();};
document.addEventListener('keydown',e=>{if(e.key==='Escape')closePairDetail();});

document.querySelectorAll('.chart-tab').forEach(tab=>{
  tab.onclick=()=>{
    document.querySelectorAll('.chart-tab').forEach(t=>t.classList.remove('active'));
    tab.classList.add('active');
    _activeTF=tab.dataset.tf;
    if(_modalData)renderCharts(_modalData);
  };
});

function closePairDetail(){
  $('pair-modal').classList.remove('open');
  Object.values(_charts).forEach(c=>{try{c.remove();}catch(e){}});
  _charts={};_modalData=null;
}

async function openPairDetail(pair){
  $('pair-modal').classList.add('open');
  $('modal-title').innerHTML=`<span style="font-size:22px">${icon(pair)}</span> ${pair} — Technical Analysis`;
  $('modal-stats').innerHTML='<div style="color:var(--dim);padding:12px">Loading data...</div>';
  $('ind-grid').innerHTML='';
  $('modal-trades').innerHTML='';
  ['price-chart','macd-chart','rsi-chart','vol-chart','pnl-chart'].forEach(id=>{$(id).innerHTML='';});

  try{
    const r=await fetch(`/api/pair-detail?pair=${encodeURIComponent(pair)}`,{credentials:'same-origin'});
    if(!r.ok)throw new Error('API error '+r.status);
    _modalData=await r.json();
    _activeTF='1h';
    document.querySelectorAll('.chart-tab').forEach(t=>t.classList.remove('active'));
    document.querySelector('.chart-tab[data-tf="1h"]').classList.add('active');
    renderModal(_modalData);
  }catch(err){
    $('modal-stats').innerHTML=`<div style="color:var(--red);padding:12px">Failed to load: ${err.message}</div>`;
  }
}

function renderModal(d){
  const ind=d.indicators_4h||{};
  const pnl=d.pnl||{};
  const comp=d.composite_score||0;
  const compCls=comp>40?'g':comp<-20?'r':'a';

  let statsH='';
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">Composite Score</div><div class="modal-stat-val ${compCls}">${typeof comp==='number'?comp.toFixed(1):comp}</div></div>`;
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">RSI(5)</div><div class="modal-stat-val">${(ind.rsi_5||ind.rsi||0).toFixed(1)}</div></div>`;
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">MACD 4H</div><div class="modal-stat-val">${(ind.macd_4h_l||ind.macd_line||0).toFixed(2)}</div></div>`;
  const ema8=ind.ema_8||0, ema21=ind.ema_21||0;
  const emaCls=ema8>ema21?'g':'r';
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">EMA 8/21</div><div class="modal-stat-val ${emaCls}">${ema8>ema21?'BULL':'BEAR'}</div></div>`;
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">ATR(14)</div><div class="modal-stat-val">${(ind.atr||0).toFixed(2)}</div></div>`;
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">Vol Ratio</div><div class="modal-stat-val">${(ind.vol_ratio_20||0).toFixed(2)}x</div></div>`;
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">Total P&L</div><div class="modal-stat-val ${pc(pnl.pnl||0)}">$${((pnl.pnl||0)>=0?'+':'')+(pnl.pnl||0).toFixed(2)}</div></div>`;
  statsH+=`<div class="modal-stat"><div class="modal-stat-label">Trades</div><div class="modal-stat-val w">${pnl.trades||0} (${pnl.wins||0}W)</div></div>`;
  $('modal-stats').innerHTML=statsH;

  const indKeys=['rsi','rsi_5','macd_line','macd_signal','macd_hist','macd_4h_l','macd_4h_s','macd_4h_h',
    'ema_8','ema_21','sma_200','vwap','atr','bb_upper','bb_lower','vol_ratio_20','adx'];
  let igH='';
  indKeys.forEach(k=>{
    if(ind[k]!=null){
      const v=typeof ind[k]==='number'?ind[k].toFixed(4):ind[k];
      igH+=`<div class="ind-item"><div class="ind-item-label">${k.replace(/_/g,' ').toUpperCase()}</div><div class="ind-item-val">${v}</div></div>`;
    }
  });
  $('ind-grid').innerHTML=igH||'<div style="color:var(--dim)">No indicator data available</div>';

  const trades=d.db_trades||[];
  if(trades.length>0){
    let th='<table><thead><tr><th>Time</th><th>Side</th><th class="rt">Entry</th><th class="rt">Exit</th><th class="rt">P&L</th><th>Status</th><th>Reason</th></tr></thead><tbody>';
    trades.forEach(t=>{
      const sideCls=t.side==='BUY'?'g':'r';
      th+=`<tr><td style="white-space:nowrap;color:var(--dim)">${(t.opened_at||'').slice(5)}</td>`+
        `<td class="${sideCls}" style="font-weight:700">${t.side}</td>`+
        `<td class="rt">${fmt(t.entry_price)}</td>`+
        `<td class="rt">${t.exit_price?fmt(t.exit_price):'—'}</td>`+
        `<td class="rt ${pc(t.pnl||0)}">${t.pnl!=null?'$'+(t.pnl>=0?'+':'')+t.pnl.toFixed(2):'—'}</td>`+
        `<td style="color:${t.status==='closed'?'var(--dim)':'var(--green)'}">${t.status}</td>`+
        `<td style="color:var(--dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.reasoning||''}</td></tr>`;
    });
    th+='</tbody></table>';
    $('modal-trades').innerHTML=th;
  }else{
    $('modal-trades').innerHTML='<div style="color:var(--dim);padding:8px;text-align:center">No trades recorded for this pair</div>';
  }

  renderCharts(d);
}

function parseCandles(raw){
  if(!raw||!raw.length)return[];
  return raw.map(c=>{
    const ts=c.start||c.timestamp||c.time||0;
    const t=typeof ts==='string'?Math.floor(new Date(ts).getTime()/1000):Number(ts);
    return{time:t,open:Number(c.open),high:Number(c.high),low:Number(c.low),close:Number(c.close),volume:Number(c.volume||0)};
  }).filter(c=>c.time>0&&!isNaN(c.open)).sort((a,b)=>a.time-b.time);
}

function renderCharts(d){
  Object.values(_charts).forEach(c=>{try{c.remove();}catch(e){}});
  _charts={};
  ['price-chart','macd-chart','rsi-chart','vol-chart','pnl-chart'].forEach(id=>{$(id).innerHTML='';});

  const LWC=window.LightweightCharts||window.lightweightCharts;
  if(!LWC){$('price-chart').innerHTML='<div style="color:var(--red);padding:20px">Chart library failed to load</div>';return;}

  const chartOpts={layout:{background:{color:'#050505'},textColor:'#888',fontSize:10},
    grid:{vertLines:{color:'#111'},horzLines:{color:'#111'}},
    crosshair:{mode:0},timeScale:{timeVisible:true,secondsVisible:false,borderColor:'#1a1a1a'},
    rightPriceScale:{borderColor:'#1a1a1a'}};

  const candles=parseCandles(_activeTF==='4h'?d.candles_4h:d.candles_1h);
  if(!candles.length){$('price-chart').innerHTML='<div style="color:var(--dim);padding:30px;text-align:center">No candle data available. Waiting for data collection...</div>';return;}

  // Price chart
  const pc1=LWC.createChart($('price-chart'),{...chartOpts,height:340});
  _charts.price=pc1;
  const cs=pc1.addCandlestickSeries({upColor:'#00C853',downColor:'#FF1744',borderUpColor:'#00C853',borderDownColor:'#FF1744',wickUpColor:'#00C853',wickDownColor:'#FF1744'});
  cs.setData(candles);

  // EMA overlays
  const ind=d.indicators_4h||{};
  if(ind.ema_8_series||ind.ema_8){
    const ema8s=pc1.addLineSeries({color:'#FF9900',lineWidth:1,title:'EMA8',priceLineVisible:false});
    if(ind.ema_8_series){ema8s.setData(ind.ema_8_series.map((v,i)=>({time:candles[candles.length-ind.ema_8_series.length+i]?.time||0,value:v})).filter(d=>d.time>0));}
  }
  if(ind.ema_21_series||ind.ema_21){
    const ema21s=pc1.addLineSeries({color:'#2196F3',lineWidth:1,title:'EMA21',priceLineVisible:false});
    if(ind.ema_21_series){ema21s.setData(ind.ema_21_series.map((v,i)=>({time:candles[candles.length-ind.ema_21_series.length+i]?.time||0,value:v})).filter(d=>d.time>0));}
  }

  // Trade markers (entries + exits)
  const trades=d.db_trades||[];
  const markers=[];
  trades.forEach(t=>{
    if(t.opened_at&&t.entry_price){
      const ts=Math.floor(new Date(t.opened_at+'Z').getTime()/1000);
      if(t.side==='BUY'){
        markers.push({time:ts,position:'belowBar',color:'#00C853',shape:'arrowUp',text:'BUY $'+Number(t.entry_price).toFixed(0)});
      }else{
        markers.push({time:ts,position:'aboveBar',color:'#FF1744',shape:'arrowDown',text:'SELL $'+Number(t.entry_price).toFixed(0)});
      }
    }
    if(t.closed_at&&t.exit_price){
      const ts2=Math.floor(new Date(t.closed_at+'Z').getTime()/1000);
      const pnlStr=t.pnl!=null?(t.pnl>=0?'+':'')+Number(t.pnl).toFixed(2):'';
      markers.push({time:ts2,position:'aboveBar',color:t.pnl>=0?'#00C853':'#FF1744',shape:'circle',text:'EXIT '+pnlStr});
    }
  });
  if(markers.length){
    markers.sort((a,b)=>a.time-b.time);
    cs.setMarkers(markers);
  }
  pc1.timeScale().fitContent();

  // MACD chart
  const mc=LWC.createChart($('macd-chart'),{...chartOpts,height:120});
  _charts.macd=mc;
  if(ind.macd_series&&ind.macd_series.length){
    const macdLine=mc.addLineSeries({color:'#00BCD4',lineWidth:1.5,title:'MACD',priceLineVisible:false});
    const sigLine=mc.addLineSeries({color:'#FF9900',lineWidth:1,title:'Signal',priceLineVisible:false});
    const histSeries=mc.addHistogramSeries({title:'Hist'});
    const ml=ind.macd_series,sl=ind.signal_series||[],hl=ind.hist_series||[];
    const off=candles.length-ml.length;
    macdLine.setData(ml.map((v,i)=>({time:candles[off+i]?.time||0,value:v})).filter(d=>d.time>0));
    if(sl.length)sigLine.setData(sl.map((v,i)=>({time:candles[off+i]?.time||0,value:v})).filter(d=>d.time>0));
    if(hl.length)histSeries.setData(hl.map((v,i)=>({time:candles[off+i]?.time||0,value:v,color:v>=0?'#00C85366':'#FF174466'})).filter(d=>d.time>0));
  }else{
    $('macd-chart').innerHTML='<div style="color:var(--dim);padding:10px;text-align:center;font-size:11px">MACD: line='+((ind.macd_4h_l||ind.macd_line||0)).toFixed(4)+' sig='+((ind.macd_4h_s||ind.macd_signal||0)).toFixed(4)+'</div>';
  }
  mc.timeScale().fitContent();

  // RSI chart
  const rc=LWC.createChart($('rsi-chart'),{...chartOpts,height:100});
  _charts.rsi=rc;
  if(ind.rsi_series&&ind.rsi_series.length){
    const rsiLine=rc.addLineSeries({color:'#a78bfa',lineWidth:1.5,title:'RSI(5)',priceLineVisible:false});
    const off=candles.length-ind.rsi_series.length;
    rsiLine.setData(ind.rsi_series.map((v,i)=>({time:candles[off+i]?.time||0,value:v})).filter(d=>d.time>0));
    const ol70=rc.addLineSeries({color:'#FF174444',lineWidth:1,lineStyle:2,priceLineVisible:false});
    const ol30=rc.addLineSeries({color:'#00C85344',lineWidth:1,lineStyle:2,priceLineVisible:false});
    const ol50=rc.addLineSeries({color:'#55555544',lineWidth:1,lineStyle:2,priceLineVisible:false});
    const rsiTimes=ind.rsi_series.map((_,i)=>candles[off+i]?.time||0).filter(t=>t>0);
    if(rsiTimes.length){
      ol70.setData(rsiTimes.map(t=>({time:t,value:70})));
      ol30.setData(rsiTimes.map(t=>({time:t,value:30})));
      ol50.setData(rsiTimes.map(t=>({time:t,value:50})));
    }
  }else{
    $('rsi-chart').innerHTML='<div style="color:var(--dim);padding:10px;text-align:center;font-size:11px">RSI(5): '+((ind.rsi_5||ind.rsi||0)).toFixed(1)+'</div>';
  }
  rc.timeScale().fitContent();

  // Volume chart
  const vc=LWC.createChart($('vol-chart'),{...chartOpts,height:80});
  _charts.vol=vc;
  const volSeries=vc.addHistogramSeries({priceFormat:{type:'volume'},priceLineVisible:false});
  volSeries.setData(candles.map(c=>({time:c.time,value:c.volume,color:c.close>=c.open?'#00C85355':'#FF174455'})));
  vc.timeScale().fitContent();

  // Cumulative P&L chart
  const dbT=d.db_trades||[];
  const closedT=dbT.filter(t=>t.status==='closed'&&t.pnl!=null);
  if(closedT.length>0){
    const plc=LWC.createChart($('pnl-chart'),{...chartOpts,height:140});
    _charts.pnl=plc;
    let cumPnl=0;
    const pnlData=closedT.map(t=>{
      cumPnl+=t.pnl;
      const ts=t.closed_at?Math.floor(new Date(t.closed_at+'Z').getTime()/1000):0;
      return{time:ts,value:Math.round(cumPnl*100)/100};
    }).filter(d=>d.time>0);
    if(pnlData.length){
      const pnlArea=plc.addAreaSeries({
        topColor:cumPnl>=0?'rgba(0,200,83,0.3)':'rgba(255,23,68,0.3)',
        bottomColor:cumPnl>=0?'rgba(0,200,83,0.05)':'rgba(255,23,68,0.05)',
        lineColor:cumPnl>=0?'#00C853':'#FF1744',lineWidth:2,priceLineVisible:false});
      pnlArea.setData(pnlData);
      const zeroLine=plc.addLineSeries({color:'#333',lineWidth:1,lineStyle:2,priceLineVisible:false});
      zeroLine.setData(pnlData.map(d=>({time:d.time,value:0})));
    }
    plc.timeScale().fitContent();
  }else{
    $('pnl-chart').innerHTML='<div style="color:var(--dim);padding:12px;text-align:center;font-size:11px">No closed trades to chart</div>';
  }

  // Sync all chart timescales
  const allC=Object.values(_charts);
  if(allC.length>1){
    allC.forEach((c,i)=>{
      c.timeScale().subscribeVisibleLogicalRangeChange(range=>{
        allC.forEach((c2,j)=>{if(i!==j)c2.timeScale().setVisibleLogicalRange(range);});
      });
    });
  }
}

async function doRebalance(e){
  e.preventDefault();
  const btn=$('rebalance-btn');
  const toast=$('rebalance-toast');
  const title=$('rt-title');
  const body=$('rt-body');

  btn.classList.add('running');
  btn.textContent='⏳ RUNNING...';
  title.textContent='⚡ Rebalancing — pulling fresh data...';
  body.innerHTML='<div style="color:var(--dim)">Fetching news, technicals, on-chain data and scoring all pairs...</div>';
  toast.classList.add('show');

  try{
    const r=await fetch('/api/rebalance',{method:'POST',credentials:'same-origin'});
    const d=await r.json();

    if(d.status==='ok'){
      title.textContent=`⚡ Rebalance Complete — ${d.pairs_evaluated} pairs scored`;
      let html='';
      const scores=d.scores||[];
      scores.sort((a,b)=>b.composite_score-a.composite_score);
      for(const s of scores){
        const cls=s.composite_score>40?'g':s.composite_score<-20?'r':'a';
        const act=s.action==='BUY'?'<span class="g">BUY</span>':s.action==='SELL'?'<span class="r">SELL</span>':'<span style="color:var(--dim)">HOLD</span>';
        const rej=s.rejected?`<span class="r" style="font-size:10px"> ✗ ${s.rejected}</span>`:'';
        html+=`<div class="rt-row"><span>${s.pair} ${act}${rej}</span><span class="${cls}">${s.composite_score>0?'+':''}${s.composite_score.toFixed(0)}</span></div>`;
      }
      const trades=d.trades_executed||[];
      if(trades.length>0){
        html+=`<div style="margin-top:6px;font-weight:700;color:var(--green);font-size:11px">${trades.length} trade(s) executed</div>`;
        for(const t of trades){
          const pnlStr=t.pnl!=null?` PnL: ${t.pnl>0?'+':''}$${(t.pnl||0).toFixed(2)}`:'';
          html+=`<div class="rt-row"><span>${t.pair} ${t.action}</span><span>${t.status}${pnlStr}</span></div>`;
        }
      }else{
        html+=`<div style="margin-top:6px;color:var(--dim);font-size:10px">No trades met thresholds</div>`;
      }
      body.innerHTML=html;
    }else{
      title.textContent='❌ Rebalance Failed';
      body.innerHTML=`<div class="r">${d.message||'Unknown error'}</div>`;
    }
  }catch(err){
    title.textContent='❌ Rebalance Error';
    body.innerHTML=`<div class="r">${err.message}</div>`;
  }finally{
    btn.classList.remove('running');
    btn.textContent='⚡ REBALANCE';
    setTimeout(()=>toast.classList.remove('show'),15000);
  }
}
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
