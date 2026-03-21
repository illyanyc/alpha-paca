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
- AI agents should use PydanticAI with structured output (Pydantic models)
- Prefer agentic swarm patterns with orchestrators, validators, and executors
- Telegram integration for real-time trade alerts and daily P&L reports
- Coinbase public endpoints for crypto market data (no auth needed); CDP PEM keys required only for trading
- Commit and push frequently to prevent data loss
- NEVER default to paper trading — always default `ALPACA_PAPER=false` (LIVE mode) in code, env, and dashboard
- Show Coinbase API key status (authenticated/market_only/unauthorized) in the web dashboard
- Stream agent thinking steps and decisions in real-time in both terminal and web UI
- Always run tests before deploying to Railway

## Learned Workspace Facts
- `backend/` contains the main FastAPI stock-trading platform
- `dashboard/` contains the Next.js management dashboard
- `crypto/` is a standalone autonomous crypto trading service with web dashboard (FastAPI-served inline HTML/JS)
- `crypto/` reads env vars from parent `../.env.local` and shares the same Railway Postgres and Redis
- Coinbase spot is long-only — SHORT signals close existing longs (go to cash); no naked short selling on spot
- Coinbase public endpoints require no auth for market data; CDP PEM keys (JWT/ES256) for trading; `coinbase-advanced-py` returns typed objects — normalize with `_to_dict()`
- Coinbase order quantities must be truncated to each product's `base_increment` precision before submission
- All crypto DB tables are prefixed `crypto_` to coexist with stock tables in the same database
- `crypto/supervisor.py` is the entry point — spawns `main.py` with Redis heartbeat monitoring
- DB tables are created via `create_tables.py` scripts (not Alembic migrations)
- The `alpaca-py` BarSet object requires subscript access (`result["BTC/USD"]`), not `.get()`; `pytz` is an undeclared dependency of `alpaca-py` — must be in requirements
- `crypto/engine/` contains strategies (momentum_breakout, mean_reversion, scalp_micro, trend_rider), backtester (hourly walk-forward), and adaptive learner (EMA-scored live trade outcomes)
- `.railwayignore` must exclude `.venv` to prevent Railway snapshot/upload timeouts; "Failed to create code snapshot" errors are often transient — retry usually works
