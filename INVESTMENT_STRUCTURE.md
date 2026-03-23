# Alpha-Paca: Full Investment Structure

**Generated:** 2026-03-23 | **Purpose:** Complete system reference for LLM analysis

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Crypto Trading System](#2-crypto-trading-system)
3. [Stock Trading System](#3-stock-trading-system)
4. [Infrastructure & Operations](#4-infrastructure--operations)
5. [Known Gaps & Issues](#5-known-gaps--issues)

---

## 1. System Overview

Alpha-Paca is a dual-asset algorithmic trading platform trading **US equities** via Alpaca and **crypto** via Coinbase Advanced Trade.

| Dimension | Stocks (`backend/`) | Crypto (`crypto/`) |
|-----------|--------------------|--------------------|
| Broker | Alpaca (paper + live) | Coinbase Advanced Trade |
| Direction | Long + Short | Long only (spot) |
| Strategy count | 5 pods (stubbed scanners) | 1 deterministic + 1 LLM-driven |
| AI role | Settings optimization only | News sentiment only (deterministic core) |
| Risk framework | Drawdown FSM, VaR, validators | RiskGuard, daily halt, anti-churn |
| Capital deployed | `CAPITAL_MAX_TRADABLE=3000` | `CRYPTO_MAX_CAPITAL=0` (full NAV) |
| Status | Infrastructure complete, scanners stubbed | Live trading on Railway |

### Capital Allocation (Current `.env.local`)

| Parameter | Value |
|-----------|-------|
| Stock max tradable | $3,000 USD |
| Stock cash reserve | 5% |
| Crypto max capital | 0 (use full account) |
| Crypto pairs | BTC/USD, ETH/USD, SOL/USD, DOGE/USD, LINK/USD |

---

## 2. Crypto Trading System

### 2.1 Architecture

**Entry point:** `crypto/supervisor.py` → spawns `crypto/main.py`

**Supervisor:** Redis heartbeat every 15s, stale threshold 90s, restart backoff 5→15→60→120→300s.

**Main loop** runs concurrent async tasks:

| Task | Interval | Role |
|------|----------|------|
| `heartbeat_loop` | 5s | Redis heartbeat, TTL 120s |
| `tick_15s` | 15s | Prices, bars (5m/1m/4h/daily), indicators, regime, portfolio sync |
| `tick_momentum_trader` | 60s | Deterministic composite scoring → RiskGuard → sizing → order execution |
| `tick_exit_manager` | 15s | ATR trailing stops, TP, signal exits, time exits, hard stops |
| `tick_swing_sniper` | 14400s (4h) | LLM-driven swing analysis → RiskGuard → sizing → order execution |
| `tick_swing_exit_check` | 300s | Swing hard stop at -5% |
| `tick_news_fast` | 10s | Serper + Tavily → Claude Haiku sentiment → cached in Redis |
| `tick_onchain` | 300s | Fear & Greed, funding rates, OI, liquidations, exchange flows |
| `tick_1h` | 3600s | Telegram hourly summary |
| `tick_24h` | 86400s | Daily Telegram report, RiskGuard daily reset |
| `tick_reconcile` | 3600s | Coinbase ↔ local DB PnL reconciliation |
| `tick_daily_backtest` | 86400s (at ~06:00 UTC) | Backtest stubs + prompt optimizer |
| `web` | Always | FastAPI dashboard on port 8080 |

### 2.2 Agent Roles

| Agent | Model | Purpose |
|-------|-------|---------|
| **MomentumTrader** | None (deterministic) | Evaluates AdaptiveMomentumStrategy, outputs BUY/SELL/HOLD per pair |
| **SwingSniper** | Claude Sonnet 4 | LLM-driven swing trade analysis (1-30 day holds), reads Redis learnings |
| **NewsScout** | Claude 3.5 Haiku | Structured sentiment classification from news articles, Redis cache TTL 60s |
| **OrderExecutor** | None | All Coinbase order submissions, DB trade/position updates, Telegram alerts |
| **RiskGuard** | None | Shared pre-trade gate for all BUY signals (SELL bypasses) |
| **Healer** | Claude (optional) | Circuit breaker: 3 failures → 60s recovery, retries with backoff [2,5,15,30,60]s |

### 2.3 Strategy: Adaptive Momentum + News Alpha

**Core principle:** Deterministic composite scoring replaces LLM-driven trade decisions. LLM is used only for news sentiment classification.

#### Composite Score Formula

```
composite = clamp(
    tech_score × 0.50 + sentiment_score × 0.30 + onchain_score × 0.20,
    -100, +100
)
```

| Action | Threshold |
|--------|-----------|
| **BUY** | composite ≥ 40 |
| **EXIT** | composite ≤ -20 |

#### Technical Layer (0–100, weight 50%)

All indicators computed on **4-hour bars**:

| Component | Parameters | Points |
|-----------|-----------|--------|
| MACD (4H) | Fast 8, Slow 17, Signal 9 | Line > signal AND line > 0 → **+25**; line > signal only → **+10** |
| MACD bullish cross | Same | **+5** |
| RSI | Period 5 | > 70 → **+20**; > 50 → **+15**; > 40 → **+5** |
| EMA cross | EMA(8) vs EMA(21) | EMA(8) > EMA(21) → **+15** |
| VWAP | Session-based cumulative | Close > VWAP → **+10** |
| Volume | 20-bar SMA ratio | > 2.0x → **+15**; > 1.5x → **+12**; > 1.2x → **+10** |
| Daily MACD filter | 12-26-9 on daily bars | Daily MACD > daily signal → **+25** |

**Entry conditions tracked:** `macd_bullish`, `rsi_momentum` (RSI(5) > 50), `ema_cross`, `above_vwap`, `volume_confirm` (ratio > 1.2), `daily_trend_up`. If all true, conviction multiplied by 1.2x.

#### Sentiment Layer (-100 to +100, weight 30%)

| Signal | Source | Points |
|--------|--------|--------|
| News sentiment | Serper/Tavily → Claude Haiku classifier | `raw_score × 50` (±50 at extremes) |
| Fear & Greed Index | Alternative.me | < 20 → **+30**; < 35 → **+15**; > 80 → **-30**; > 65 → **-15** |
| BTC funding rate | CoinGlass | < -0.03% → **+20**; < -0.01% → **+10**; > 0.05% → **-20**; > 0.03% → **-15**; > 0.01% → **-5** |

#### On-Chain Layer (-100 to +100, weight 20%)

| Signal | Source | Points |
|--------|--------|--------|
| Exchange flows | CoinGlass / aggregate | Outflow → **+30**; inflow → **-30**; slight variants ±15 |
| OI + funding combo | CoinGlass | OI rising + funding < -0.01% → **+30** (short squeeze); OI rising + funding > 0.1% → **-30** |
| Liquidation cascade | CoinGlass | > $50M/1h liquidated → **-20** |
| Order book imbalance | Coinbase L2 websocket | > 0.5 → **+40**; > 0.3 → **+25**; > 0.15 → **+10**; mirrored negatives (currently inactive — `microstructure={}` in production) |

### 2.4 Indicators Computed (`engine/indicators.py`)

| Indicator | Parameters | Used By |
|-----------|-----------|---------|
| RSI | Period 5 (also 14 computed) | Strategy: RSI(5) |
| MACD (4H) | 8-17-9 fast/slow/signal | Strategy: primary signal |
| MACD (daily) | 12-26-9 | Strategy: trend filter |
| EMA | 8, 21 (also 9, 13, 34, 55) | Strategy: cross signal |
| VWAP | Cumulative TP×V / cumulative V | Strategy: bias confirmation |
| ATR | Period 14, Wilder-style EWM | Sizing, stops, exits |
| Bollinger Bands | Period 20, 2σ | Computed but not in primary strategy |
| Volume SMA | 20-bar | Strategy: volume confirmation |
| Williams %R | Period 14 | Computed but not in primary strategy |
| Keltner Channels | 20 EMA, 14 ATR, 2× | Computed but not in primary strategy |

### 2.5 Risk Management (`agents/risk_guard.py`)

**Applies to BUY only.** SELL signals bypass all gates.

| Rule | Parameter | Default |
|------|-----------|---------|
| Daily loss halt | `daily_loss_halt_pct` | 2% of NAV → halt all trading for the day |
| Max drawdown | `max_drawdown_pct` | 10% → circuit breaker |
| Total exposure cap | — | 100% of NAV |
| Max concurrent positions (per bot) | `max_concurrent_per_bot` | 3 |
| Max concurrent positions (total) | `max_concurrent_total` | 3 |
| Max risk per trade | `max_risk_per_trade_pct` | 1.5% |
| Min R:R ratio (momentum) | `day_min_rr_ratio` | 1.5:1 |
| Min R:R ratio (swing) | `swing_min_rr_ratio` | 2.0:1 |
| Anti-churn: momentum interval | `momentum_eval_interval_sec × 2` | 120s minimum between trades |
| Anti-churn: swing interval | `swing_min_trade_interval_sec` | 3600s (1h) minimum |
| Consecutive loss cooldown | `cooldown_after_losses` | 3 losses → interval doubles |
| Consecutive loss halt | `cooldown_halt_after_losses` | 5 losses → halt 2 hours |
| Trading hours (UTC) | `trading_hours_start` / `end` | 13:00–23:00 UTC |
| Correlated exposure | BTC/ETH/SOL/XRP group | Group MV / NAV ≥ 50% → reject new buys |

### 2.6 Position Sizing (`engine/leverage_sizer.py`)

**Formula (fixed fractional to ATR stop):**

```
risk_amount = NAV × (max_risk_per_trade_pct / 100)        # default 1.5% of NAV
stop_distance = ATR(14) × atr_stop_multiplier              # default 2.0 × ATR
stop_distance_pct = stop_distance / entry_price
raw_notional = risk_amount / stop_distance_pct
```

**Multipliers applied:**

| Condition | Scalar |
|-----------|--------|
| ATR% > 5% of price | 0.5× |
| ATR% > 4% | 0.6× |
| ATR% > 3% | 0.75× |
| ATR% ≤ 3% | 1.0× |
| ≥ 5 consecutive losses (bot) | 0.3× |
| ≥ 3 consecutive losses | 0.5× |
| ≥ 2 consecutive losses | 0.7× |

**Caps:**
- Max single position: **33% of NAV**
- Minimum notional: **$10**

**Capital source:**
- Momentum: `min(NAV, max_capital)` if max_capital > 0, else full NAV
- Swing: `min(cash, max_capital)` if max_capital > 0, else full cash

### 2.7 Exit Management (`engine/exit_manager.py`)

**Registration on entry:** `initial_stop = entry - ATR × 2.0`, `take_profit = entry + ATR × 3.0`

**Trailing stop:** On new high since entry, `new_stop = high - ATR × 2.0` (only ratchets up, never down)

**Exit priority (first match wins):**

| Priority | Exit Type | Condition |
|----------|-----------|-----------|
| 1 | Trailing stop | Price ≤ trailing stop |
| 2 | Take profit | Price ≥ take profit target |
| 3 | MACD signal exit | `macd_4h_bearish_cross` is true |
| 4 | RSI breakdown | RSI(5) < 40 |
| 5 | Composite exit | Composite score < -20 for the pair |
| 6 | Time exit | Hour ≥ 23 UTC (momentum bot only) |
| 7 | Hard stop | Unrealized PnL ≤ -3% from avg entry (backup if ATR stop is wider) |

**Swing-specific:** Separate check closes at **-5% PnL** via market sell.

### 2.8 Order Execution

**BUY (Momentum with target/stop):**
1. Try **bracket order**: `limit_limit_gtc` at mid price, `post_only=true`, attached `trigger_bracket_gtc` (TP limit + SL stop-trigger)
2. If bracket fails → fallback **limit order** at mid, `post_only=true`
3. `wait_for_fill`: poll up to 60s, 1s interval
4. If cancelled/expired on limit → fallback **market buy**
5. If still cancelled/expired → abort

**BUY (Swing / no TP-SL):** Market buy immediately.

**SELL:** Always market sell. Quantity from DB position, truncated to Coinbase `base_increment`.

**Fill validation:** If `filled_qty ≤ 0` or `filled_avg_price ≤ 0` → skip PnL recording. Cancelled/expired orders never record PnL.

**Coinbase API:**
- Auth: CDP PEM keys (JWT/ES256)
- Quantity: truncated to product `base_increment` precision
- Limit prices: 2 decimal places

### 2.9 Trading Universe

| Pair | Role |
|------|------|
| BTC/USD | Core — all-weather, highest liquidity |
| ETH/USD | Core — best risk-adjusted momentum |
| SOL/USD | Satellite — highest volatility |
| DOGE/USD | Satellite — configured in env |
| LINK/USD | Satellite — configured in env |

Fixed list from `CRYPTO_PAIRS` env var. No dynamic pair scanner.

### 2.10 Services

| Service | Source | Polling | Cache |
|---------|--------|---------|-------|
| News (Serper) | serper.dev | 10s | Circuit breaker: 2 fails → 600s cooldown |
| News (Tavily) | tavily.com | 10s | Same circuit breaker |
| Fear & Greed | alternative.me | 300s | — |
| Funding/OI/Liquidations | coinglass.com | 300s | — |
| Exchange flows | CoinGlass aggregate | 300s | — |
| Prices | Coinbase REST | 15s | Redis `crypto:prices`, TTL 120s |
| Telegram | python-telegram-bot | Event-driven | Disabled if token/chat missing |

### 2.11 Database Tables (PostgreSQL, `crypto_` prefix)

| Table | Purpose |
|-------|---------|
| `crypto_trades` | Per fill: bot_id, pair, side, qty, entry/exit price, PnL, slippage, confidence, reasoning, TP/SL, exchange_order_id |
| `crypto_positions` | Unique `(pair, bot_id)`: qty, avg entry, current price, MV, unrealized PnL |
| `trade_journal` | Decision audit: action, conviction, indicators/regime JSON, portfolio snapshots |
| `crypto_signals` | Signal log (technical/news/fundamental) |
| `crypto_prices` | Price tick history |
| `crypto_agent_health` | Agent health events |
| `crypto_portfolio_state` | Portfolio snapshots |

### 2.12 Backtesting & Prompt Optimization

**Backtester (`engine/backtester_v2.py`):**
- Momentum replay: 5m candles from bar 60+, sample every 10 bars, 20% capital per BUY
- Swing replay: 4h candles from bar 30+, 30% capital per BUY
- Simulated costs: 60 bps fee + 10 bps slippage = 70 bps per leg
- Metrics: Sharpe (annualized), max drawdown, win rate

**Prompt Optimizer (`agents/prompt_optimizer.py`):**
- Model: Claude Sonnet 4
- Input: last 7 days of trades (up to 200), current learnings
- Output: `PromptLearning` rows (bot_id, learning, confidence, sample_size)
- Storage: Redis `crypto:learnings:{bot_id}`, max 20 strings per key
- SwingSniper reads learnings from Redis into its prompt; MomentumTrader does not

**Daily schedule:** Runs at ~06:00 UTC, processes first 2 pairs only.

### 2.13 Web Dashboard

**Auth:** Cookie session from `DASHBOARD_PASSWORD`

**Live data:** WebSocket `/ws` for streaming updates

**Features:**
- Real-time portfolio NAV, cash, exposure, drawdown, daily/total PnL, win rates
- Live prices with SVG sparkline trends
- Per-pair detail modal: candlestick charts, EMA overlays, MACD/RSI/Volume subplots, entry/exit markers, cumulative P&L (TradingView Lightweight Charts)
- Position tracking by bot_id
- Trade history
- Strategy signal display
- Agent status/health monitoring
- Settings management (exchange + trading)

---

## 3. Stock Trading System

### 3.1 Architecture

**Entry point:** `backend/app/main.py` — FastAPI app, no in-process scheduler.

**Orchestrator (`backend/app/tasks/orchestrator.py`):**
- Holds: `SignalProcessor`, `FactorModel`, `AlphaModel` (constructed but unused), `OrderManager`, `PositionManager`
- 5 strategy pods: Momentum, Mean Reversion, Event-Driven, Sector Rotation, Stat Arb

**Execution flow:**
1. `run_pre_market(universe)` → each pod scans the universe
2. `run_intraday(scan_results)` → generate signals → validate → size → submit
3. `run_post_market()` → sync positions → update PnL → check exits

**Scheduling:** External — not implemented in this codebase. Something must call these methods.

**NAV source:** `max_tradable` from settings if > 0, else hard-coded $10,000 fallback (not live Alpaca equity in orchestrator).

### 3.2 Strategy Pods

#### Pod Allocations (% of capital)

| Pod | Allocation | Status |
|-----|-----------|--------|
| Momentum | 25% | Scanner stubbed (returns zeros) |
| Mean Reversion | 20% | Scanner stubbed |
| Event-Driven | 25% | Scanner stubbed |
| Sector Rotation | 20% | Scans fixed ETF list (stubbed scores) |
| Stat Arb | 0% (disabled) | Cointegration stubbed |

#### Signal Qualification Gate (shared)

All signals must pass `BasePod.validate_signal`:
- `|composite_score| ≥ 0.01`
- `ic_weight ≥ min_signal_ic` (default 0.03)

**Critical issue:** All signal generators set `ic_weight: 0.0`, so every signal fails this gate.

#### Momentum Pod

| Parameter | Value |
|-----------|-------|
| RSI period | 14 |
| MACD | 12/26/9 |
| Breakout lookback | 20 bars |
| Entry: Long | RSI > 70 AND MACD hist > 0 → score +0.4 |
| Entry: Short | RSI < 30 AND MACD hist < 0 → score +0.3 |
| Breakout bonus | +0.3 |
| Momentum score | +momentum_score × 0.3 |
| Min score | 0.1 |
| Stop loss | 3% from entry |
| Take profit targets | 1.5×, 2.5×, 3.5× risk |

#### Mean Reversion Pod

| Parameter | Value |
|-----------|-------|
| Bollinger period | 20, 2σ |
| Entry threshold | \|bb_z\| ≥ 1.5 |
| Long | bb_z < -1.5 (below lower band) |
| Short | bb_z > 1.5 (above upper band) |
| RSI bonus | RSI < 30 (long) or > 70 (short) → +0.2 |
| Score | min(\|bb_z\|/3, 1.0) + RSI bonus |
| Stop loss | 4% from entry |
| Take profit targets | 1×, 2× risk |

#### Event-Driven Pod

| Parameter | Value |
|-----------|-------|
| Surprise threshold | \|surprise_pct\| ≥ 5% → +0.3 |
| Sentiment bonus | > 0.5 → +0.2; < -0.5 → +0.15 |
| Long condition | surprise ≥ 0 AND sentiment ≥ 0 |
| Short condition | Otherwise |
| Min score | 0.1 |
| Stop loss | 5% from entry |
| Take profit targets | 1.5×, 3.0× risk |
| Urgency | High if \|surprise\| ≥ 5% |

#### Sector Rotation Pod

| Parameter | Value |
|-----------|-------|
| Lookback | 20 days |
| Universe | Fixed ETF list: XLK, XLV, XLF, XLE, XLY, XLP, XLI, XLB, XLU, XLRE, XLC |
| Long signal | relative_strength > 0.10 |
| Short signal | relative_strength < -0.10 |
| Score | min(\|rs\|/0.30, 1.0) |
| Min score | 0.1 |
| Stop loss | 4% from entry |
| Take profit targets | 1.5×, 2.5× risk |

#### Stat Arb Pod (disabled, 0% allocation)

| Parameter | Value |
|-----------|-------|
| Cointegration p-value threshold | 0.05 |
| Half-life range | 1–60 days |
| Entry threshold | \|spread_z\| ≥ 2.0 |
| Exit threshold | spread_z = 0.5 |
| Default stop | z = 3.5 |
| Status | Fully stubbed — `_test_pair` returns p_value=1.0 |

### 3.3 Risk Management

#### Pre-Trade Validators

| Validator | Parameter | Default |
|-----------|-----------|---------|
| Min average volume | `PRETRADE_MIN_AVG_VOLUME` | 500,000 shares |
| Min average dollar volume | `PRETRADE_MIN_AVG_DOLLAR_VOL` | $5,000,000 |
| Max spread | `PRETRADE_MAX_SPREAD_PCT` | 0.10 |
| Max pairwise correlation | `PRETRADE_MAX_PAIRWISE_CORRELATION` | 0.80 |

#### Risk Gate

| Rule | Limit |
|------|-------|
| Max single position | 5% of NAV |
| Max concurrent positions | 12 |
| Max gross exposure | 120% |
| Max net exposure | 80% |
| Max factor exposure | 0.3 per factor |
| Max daily VaR | 2.0% |
| Pod correlation overlap | > 0.60 → warn only (does not block) |

#### Drawdown Finite State Machine

| State | Drawdown Threshold | Position Scale |
|-------|-------------------|----------------|
| NORMAL | — | 1.0× (full sizing) |
| REDUCED | ≥ 1.5% | 0.5× |
| HALTED | ≥ 3.0% | 0.0× (no new trades) |
| PANIC | ≥ 5.0% | 0.0× (no new trades) |

Additional thresholds defined but **not implemented**: weekly halt 5%, monthly halt 8%.

#### In-Trade Monitoring

- Stop hit → fail/close
- Target hit → warn
- Max hold time → 120 hours (5 days), warn only

#### Stress Testing

Named scenarios with equity shocks: -8%, -10%, -15%, -34%, -40%. Loss calculated as `market_value × equity_shock × beta` per position.

### 3.4 Position Sizing

```
dollar_risk = NAV × (risk_per_trade_pct / 100)     # default 1.0%
risk_per_share = |entry_price - stop_price|
shares = dollar_risk / risk_per_share
position_value = shares × entry_price
capped at max_position_pct of NAV                    # default 5%
```

| Parameter | Default |
|-----------|---------|
| Risk per trade | 1.0% of NAV |
| Max single position | 5% of NAV |
| Max concurrent positions | 12 |
| Max positions per pod | 4 |
| Max event-driven positions | 2 (defined but not enforced) |

### 3.5 Order Execution

**Current state:** Stub implementation.
- `OrderManager` builds in-memory limit orders at signal entry price
- `AlpacaService` wraps real `alpaca-py` SDK (TradingClient + StockHistoricalDataClient)
- AlpacaService is **not called** from OrderManager in the current flow
- Paper mode: `ALPACA_PAPER=false` (live mode is default per env)

**Paper-to-live graduation gates (settings only, not automated):**
- 30 days paper trading
- Sharpe ≥ 1.0
- ≥ 50 trades
- 7 shadow days

### 3.6 Trading Universe

**UniverseBuilder** (`services/universe_builder.py`):
- Source: FMP `company_screener`
- Filters: min avg volume 500K, min market cap $300M, US only, no ETFs/funds, limit 500
- **Not called** from orchestrator — expects caller-supplied universe list

### 3.7 AI / Settings Optimization

**Only AI use in backend:** `services/settings_optimizer.py`
- Model: Claude Sonnet 4
- Input: last 30 days trades, pod performance, backtest results, IC tracking, portfolio state, current hot config
- Output: `SettingSuggestion` list with bounded conservative deltas
- Triggered: `POST /api/settings/optimize`

### 3.8 Hot Config (Redis + PostgreSQL)

Settings persist in PostgreSQL `settings` table with Redis snapshot cache (`alphapaca:hot_config:snapshot`). Tunable categories:

- Capital: max_tradable, reserve_cash_pct
- Pod allocations: momentum, mean_reversion, event_driven, sector_rotation, stat_arb
- Risk: target_beta, factor exposure, VaR, stress loss, gross/net exposure, pod correlation
- Position sizing: risk_per_trade, max_position, max_concurrent
- Drawdown: reduced/halted/panic thresholds
- Execution: fill timeout, retries
- Pre-trade: volume, spread, correlation
- Signal: IC threshold, OOS samples, win rate, profit factor

**Known issue:** Hot config key format `_hc_{full_key}` doesn't match validator lookup format `_hc_{short_key}`, so overrides may not propagate.

### 3.9 Database Tables (PostgreSQL)

| Table | Purpose |
|-------|---------|
| `portfolio_state` | Portfolio snapshots |
| `positions` | Open positions |
| `orders` | Order records with optional bracket parent |
| `trades` | Filled trade records |
| `pod_allocations` | Per-pod capital allocation |
| `pod_signals` | Signal records per pod |
| `pod_performance` | Pod-level performance metrics |
| `pod_overlap` | Cross-pod correlation tracking |
| `factor_exposures` | Factor exposure snapshots |
| `var_history` | VaR calculation history |
| `stress_test_results` | Stress test outputs |
| `risk_events` | Risk event log |
| `drawdown_state` | Drawdown FSM state |
| `signal_ic_tracking` | Signal information coefficient tracking |
| `backtest_results` | Backtest result records |
| `correlation_matrix` | Position correlation snapshots |
| `universe_snapshots` | Universe composition history |
| `watchlist` | User watchlist |
| `news_cache` | Cached news articles |
| `scan_results` | Scanner output records |
| `agent_health` | Agent health monitoring |
| `validation_log` | Validation decision log |
| `settings` | Hot config persistence |
| `users` | Dashboard users |

### 3.10 Dashboard API

| Route | Endpoints |
|-------|-----------|
| `/api/auth` | POST `/login`, GET `/me` (JWT) |
| `/api/portfolio` | GET `/state`, `/history` |
| `/api/positions` | GET `/`, `/{position_id}` |
| `/api/pods` | GET `/`, `/{pod_name}` (allocation + signals + performance) |
| `/api/signals` | GET `/` (filters: pod, limit ≤ 500), `/{signal_id}` |
| `/api/trades` | GET `/stats`, `/` |
| `/api/risk` | GET `/var`, `/factors`, `/stress-tests`, `/events`, `/drawdown` |
| `/api/backtest` | GET `/results`, `/results/{result_id}` |
| `/api/settings` | GET/PUT `/tunable`, POST `/optimize`, GET `/graduation` |
| WebSocket `/ws` | Portfolio state every 5s |

---

## 4. Infrastructure & Operations

### 4.1 Deployment

| Component | Platform | Notes |
|-----------|----------|-------|
| Crypto service | Railway | `supervisor.py` entry, `.railwayignore` excludes `.venv` |
| Backend API | Railway | FastAPI |
| Dashboard | Railway / Vercel | Next.js 14+ |
| Database | Railway PostgreSQL | Shared between stock and crypto (prefix `crypto_` for crypto tables) |
| Cache | redis.io (US East) | Shared between stock and crypto |

### 4.2 External Services & Costs

| Service | Purpose | Estimated Monthly Cost |
|---------|---------|----------------------|
| Tavily API | News search | ~$149 |
| Serper.dev | News backup | ~$50 |
| Anthropic Claude | News sentiment + swing analysis + settings optimization | ~$30–100 |
| CoinGlass | Derivatives data (OI, funding, liquidations) | Free tier / ~$50 |
| Alternative.me | Fear & Greed Index | Free |
| FMP | Stock fundamentals + screening | Existing subscription |
| Alpaca | Stock brokerage | Commission-free |
| Coinbase | Crypto brokerage | Fee-tier dependent (target Advanced 1: 0.25% maker) |

### 4.3 Monitoring

- Redis heartbeat (crypto supervisor)
- Agent health events in DB
- Healer circuit breaker with optional AI diagnosis
- Telegram alerts: trade fills, hourly summaries, daily reports, errors
- Web dashboard: real-time portfolio, positions, agent statuses

---

## 5. Known Gaps & Issues

### 5.1 Crypto System

| Issue | Impact | Severity |
|-------|--------|----------|
| Order book imbalance signals inactive (`microstructure={}`) | On-chain layer missing ~40 potential points from book imbalance | Medium |
| Daily backtest runs with no-op agent function | Metrics are no-trade baselines, not actual strategy replay | Low |
| MomentumTrader does not read prompt optimizer learnings | Only SwingSniper benefits from continuous improvement | Medium |
| `config.py` MACD/RSI params exist but `compute_all` hardcodes values | Changing env vars doesn't change indicator calculation | Low |
| `primary_window_end` (default 17) defined but unused | No reduced entry window enforcement | Low |
| `max_leverage` (default 5.0) in config but not enforced in sizer | Setting has no effect | Low |

### 5.2 Stock System

| Issue | Impact | Severity |
|-------|--------|----------|
| All scanners return placeholder zeros | No signals generated in production | **Critical** |
| Signal `ic_weight=0.0` fails `validate_signal` gate (min 0.03) | All signals rejected even if scanners worked | **Critical** |
| Orchestrator context uses stub zeros (volume, spread, exposures) | Pre-trade liquidity validator blocks all trades | **Critical** |
| `AlpacaService` not connected to `OrderManager` | Orders never reach Alpaca | **Critical** |
| No scheduler in codebase | Orchestrator methods never called | **Critical** |
| `_get_nav()` falls back to $10,000 hardcode | Not using real account equity | High |
| Hot config key name mismatch for validators | Setting overrides may not propagate | Medium |
| `SignalProcessor`, `FactorModel`, `AlphaModel` constructed but unused | Dead code in orchestrator | Low |
| Weekly/monthly drawdown halt thresholds defined but not enforced | Only per-trade drawdown FSM works | Medium |
| Earnings helper scoring not wired into scanner/signal path | Event-driven pod ignores earnings analytics | Medium |

### 5.3 Cross-System

| Issue | Impact |
|-------|--------|
| Anthropic API keys are workspace-scoped — key may belong to unfunded workspace | News sentiment and swing analysis fail with "credit balance too low" |
| Shared PostgreSQL with no migration framework (manual `create_tables.py`) | Schema drift risk between environments |
| No automated test suite for live trading paths | Regressions in order execution go undetected until live |
