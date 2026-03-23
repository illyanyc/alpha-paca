# Alpha-Paca Agent Rules

## Workspace Boundary
- The project root is `/Users/illya/dev/alpha-paca/` — NEVER operate outside this directory
- The parent `/Users/illya/dev/` is a separate, unrelated repo (debtreliefkarma.com) — do not read, write, or run git commands there
- All git operations must target the alpha-paca repo only (`/Users/illya/dev/alpha-paca/.git`)
- All shell commands must run from within `/Users/illya/dev/alpha-paca/` or its subdirectories
- This rule has been violated before — treat it as the highest priority boundary

## Git
- Remote: `git@github.com:illyanyc/alpha-paca.git` (private)
- Default branch: `main`
- Never commit `.env.local` or `.env` (secrets)
- Always commit after major changes — catastrophic data loss has occurred from uncommitted work

## Stack
- Backend: FastAPI (Python 3.12), async SQLAlchemy, Pydantic v2, PydanticAI
- Dashboard: Next.js 14+ App Router, TypeScript, Tailwind CSS, shadcn/ui
- Database: PostgreSQL on Railway (used for both local and production — never use local Docker Postgres)
- Cache: Redis on redis.io
- Broker: Alpaca (stocks, paper + live), Coinbase Advanced Trade (crypto, CDP PEM/JWT auth)
- Data: FMP (stable endpoints), Serper.dev, Tavily — no Polygon API available
- AI: Anthropic Claude via PydanticAI for all agent-based features
- Package manager: `uv` (Python), `npm` (Node)

## Learned User Preferences
- Slider + editable number input for all numeric settings in the dashboard
- Hot-swappable settings stored in Redis with env vars as initial defaults — must persist across Railway redeploys
- Railway Postgres for local development (not a local Docker instance)
- AI agents should use PydanticAI with structured output (Pydantic models); prefer agentic swarm patterns with orchestrators, validators, and executors
- Telegram integration for real-time trade alerts and daily P&L reports
- Coinbase public endpoints for market data (no auth); CDP PEM keys for trading; show API key status in dashboard
- Commit and push frequently to prevent data loss
- NEVER default to paper trading — always default `ALPACA_PAPER=false` (LIVE mode)
- Stream agent thinking steps and decisions in real-time in both terminal and web UI
- Always run tests before deploying to Railway
- Dashboard should look like a Bloomberg Terminal — full-width, crypto icons per asset, count badges, larger fonts, real-time NAV (every 2s), mobile-optimized with clear tap targets (chart buttons per row, not just click-on-row)
- Prefer deterministic rule-based trading over LLM-driven entries — use LLM only for news sentiment classification, not core buy/sell decisions

## Learned Workspace Facts
- `backend/` contains a 5-pod stock-trading platform (Momentum, Mean Reversion, Event-Driven, Sector Rotation, Stat Arb) with risk validators and hot-config; scanners remain stubbed — infrastructure is production-grade but live data pipeline needs completion
- `dashboard/` contains the Next.js management dashboard
- `crypto/` is a standalone autonomous crypto trading service with a deterministic MomentumTrader (primary) + SwingSniper (secondary, LLM-driven), shared RiskGuard, ExitManager, and a FastAPI-served web dashboard with TradingView Lightweight Charts
- `crypto/` reads env vars from parent `../.env.local`; `main.py` must `load_dotenv()` before PydanticAI agent imports; shares Railway Postgres and Redis
- `crypto/engine/strategies.py` has a single `AdaptiveMomentumStrategy` with deterministic composite scoring (-100 to +100): Technical 50%, News/Sentiment 30%, On-Chain 20%; BUY threshold > 40, EXIT threshold < -20
- `crypto/engine/exit_manager.py` manages ATR trailing stops (2.0 × ATR(14)), take profit (3.0 × ATR), time exits (23:00 UTC), signal exits (MACD bearish cross, RSI < 40), and hard stop (-3%)
- `crypto/engine/leverage_sizer.py` uses ATR-based fixed-fractional sizing (1.5% risk per trade), capped at 33% of NAV
- Coinbase spot is long-only (SHORT signals close existing longs); supports limit orders with `post_only=true` and bracket orders (`trigger_bracket_gtc` for TP/SL); quantities must be truncated to each product's `base_increment` precision
- All crypto DB tables are prefixed `crypto_` to coexist with stock tables in the same database
- `crypto/supervisor.py` is the entry point — spawns `main.py` with Redis heartbeat monitoring; DB tables are created via `create_tables.py` scripts (not Alembic migrations)
- Order executor must verify fill status (`filled` vs `cancelled/expired`) before recording PnL — zero-fill cancelled orders cause phantom losses if processed blindly; always reconcile Coinbase trade history with local DB
- `.railwayignore` must exclude `.venv` to prevent Railway snapshot/upload timeouts; Anthropic API keys are workspace-scoped — "credit balance too low" errors may occur even with account-level credits if the key belongs to a different workspace
