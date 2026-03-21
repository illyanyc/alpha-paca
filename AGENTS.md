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
- Broker: Alpaca (paper + live, stocks and crypto)
- Data: FMP (stable endpoints), Serper.dev, Tavily — no Polygon API available
- AI: Anthropic Claude via PydanticAI for all agent-based features
- Package manager: `uv` (Python), `npm` (Node)

## Learned User Preferences
- Slider + editable number input for all numeric settings in the dashboard
- Hot-swappable settings stored in PostgreSQL, cached in Redis, env vars as initial defaults
- Railway Postgres for local development (not a local Docker instance)
- AI agents should use PydanticAI with structured output (Pydantic models)
- Prefer agentic swarm patterns with orchestrators, validators, and executors
- Terminal-only services (no dashboard) are acceptable for standalone trading services
- Telegram integration for real-time trade alerts and daily P&L reports
- When fetching Alpaca crypto market data, use no-auth client (free tier)
- Commit and push frequently to prevent data loss

## Learned Workspace Facts
- `backend/` contains the main FastAPI stock-trading platform
- `dashboard/` contains the Next.js management dashboard
- `crypto/` is a standalone autonomous crypto trading service (agent swarm, terminal-only)
- `crypto/` reads env vars from parent `../.env.local` and shares the same Railway Postgres and Redis
- Alpaca crypto is long-only (no short selling) — bearish signals sell to cash instead
- Alpaca crypto data (quotes, bars) works without authentication; trading requires API keys
- Alpaca API keys may expire — handle 401 errors gracefully with fallback defaults
- All crypto DB tables are prefixed `crypto_` to coexist with stock tables in the same database
- `crypto/supervisor.py` is the entry point — spawns `main.py` with Redis heartbeat monitoring
- DB tables are created via `create_tables.py` scripts (not Alembic migrations)
- The `alpaca-py` BarSet object requires subscript access (`result["BTC/USD"]`), not `.get()`
- Dashboard API base URL uses `NEXT_PUBLIC_API_URL` env var with `/api` suffix
