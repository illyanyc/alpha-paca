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
- Dashboard should look like a Bloomberg Terminal — full-width, crypto icons per asset, count badges, larger fonts, real-time NAV (every 2s), mobile-optimized
- Trade aggressively within risk-managed bounds — conviction-based sizing, daily loss halts, anti-churn cooldowns

## Learned Workspace Facts
- `backend/` contains the main FastAPI stock-trading platform
- `dashboard/` contains the Next.js management dashboard
- `crypto/` is a standalone autonomous crypto trading service with two AI bots (SwingSniper + DaySniper), shared RiskGuard, and a FastAPI-served web dashboard
- `crypto/` reads env vars from parent `../.env.local`; `main.py` must `load_dotenv()` before PydanticAI agent imports; shares Railway Postgres and Redis
- Coinbase spot is long-only (SHORT signals close existing longs); public endpoints need no auth; CDP PEM keys (JWT/ES256) for trading; normalize `coinbase-advanced-py` typed objects with `_to_dict()`
- Coinbase order quantities must be truncated to each product's `base_increment` precision before submission
- All crypto DB tables are prefixed `crypto_` to coexist with stock tables in the same database
- `crypto/supervisor.py` is the entry point — spawns `main.py` with Redis heartbeat monitoring
- DB tables are created via `create_tables.py` scripts (not Alembic migrations)
- `crypto/engine/` has 8 institutional strategies, HMM regime detection, and multi-timeframe confluence; `risk_guard.py` enforces daily loss halts, drawdown breakers, anti-churn; `leverage_sizer.py` sizes by conviction + ATR; `backtester_v2.py` + `prompt_optimizer.py` run daily for continuous improvement
- Order executor must verify fill status (`filled` vs `cancelled/expired`) before recording PnL — zero-fill cancelled orders cause phantom losses if processed blindly
- `.railwayignore` must exclude `.venv` to prevent Railway snapshot/upload timeouts; "Failed to create code snapshot" errors are often transient — retry usually works
