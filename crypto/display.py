"""Rich terminal UI for Alpha-Paca Crypto — Bloomberg Terminal-style with regime, strategies, agent thinking."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

SPARK_CHARS = "▁▂▃▄▅▆▇█"
BULL_COLOR = "green"
BEAR_COLOR = "red"
NEUTRAL_COLOR = "yellow"

REGIME_STYLES = {
    "trending_up": ("bold green", "TREND-UP ↑"),
    "trending_down": ("bold red", "TREND-DOWN ↓"),
    "mean_reverting": ("bold blue", "MEAN-REVERT ↔"),
    "volatile": ("bold yellow", "VOLATILE ⚡"),
}


def _spark_line(values: list[float], width: int = 30) -> str:
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    sampled = values[-width:] if len(values) > width else values
    return "".join(SPARK_CHARS[min(int((v - mn) / rng * 7), 7)] for v in sampled)


def _pnl_color(val: float) -> str:
    if val > 0:
        return BULL_COLOR
    elif val < 0:
        return BEAR_COLOR
    return NEUTRAL_COLOR


def _signal_style(signal: str) -> str:
    s = signal.lower()
    if "strong_buy" in s or "buy" in s:
        return "green"
    if "strong_sell" in s or "sell" in s:
        return "red"
    return "yellow"


def _bar(value: float, max_val: float, width: int = 20, char: str = "█") -> str:
    if max_val <= 0:
        return ""
    filled = int(abs(value) / max_val * width)
    return char * min(filled, width)


def build_header(mode: str = "LIVE", uptime_sec: int = 0, regime: dict | None = None,
                 exchange_status: str = "checking", onchain: dict | None = None) -> Panel:
    hrs = uptime_sec // 3600
    mins = (uptime_sec % 3600) // 60
    secs = uptime_sec % 60
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = Text()
    header.append("  ALPHA-PACA ", style="bold bright_yellow")
    header.append("│ ", style="dim")

    if mode == "LIVE":
        header.append("LIVE", style="bold red")
    else:
        header.append(mode, style="bold green")

    header.append(f"  │  Up: {hrs:02d}:{mins:02d}:{secs:02d}", style="dim")
    header.append(f"  │  {now}", style="dim")

    if exchange_status == "connected":
        header.append("  │  ", style="dim")
        header.append("● CB", style="bold green")
    elif exchange_status == "unauthorized":
        header.append("  │  ", style="dim")
        header.append("● CB AUTH FAIL", style="bold red")
    elif exchange_status == "market_only":
        header.append("  │  ", style="dim")
        header.append("● MKT ONLY", style="bold yellow")

    if regime:
        r_name = regime.get("regime", "volatile")
        r_conf = regime.get("confidence", 0)
        style, label = REGIME_STYLES.get(r_name, ("bold yellow", r_name.upper()))
        header.append("  │  ", style="dim")
        header.append(f"[{label}]", style=style)
        header.append(f" {r_conf:.0%}", style="dim")

    if onchain and onchain.get("fear_greed_index"):
        fg = onchain["fear_greed_index"]
        fg_label = onchain.get("fear_greed_label", "")
        fg_style = "red" if fg < 25 else "yellow" if fg < 45 else "dim" if fg < 55 else "green" if fg < 75 else "red"
        header.append(f"  │  F&G: ", style="dim")
        header.append(f"{fg}", style=fg_style)
        header.append(f" {fg_label}", style="dim")

    return Panel(header, border_style="bright_yellow", padding=(0, 0))


def build_portfolio_panel(portfolio: dict, positions: list[dict]) -> Panel:
    nav = portfolio.get("nav", 0)
    cash = portfolio.get("cash", 0)
    exposure = portfolio.get("total_exposure_pct", 0)
    unrealized = portfolio.get("unrealized_pnl", 0)
    drawdown = portfolio.get("drawdown_pct", 0)
    realized = portfolio.get("realized_pnl_today", 0)
    total_pnl = portfolio.get("total_realized_pnl", 0)
    total_trades = portfolio.get("total_trades", 0)
    total_wr = portfolio.get("total_win_rate", 0)

    grid = Table.grid(padding=(0, 2), expand=True)
    for _ in range(8):
        grid.add_column(justify="center")

    grid.add_row(
        f"[bold]NAV[/]\n[white]${nav:,.2f}[/]",
        f"[bold]Cash[/]\n[white]${cash:,.2f}[/]",
        f"[bold]Expo[/]\n[{'green' if exposure < 70 else 'yellow' if exposure < 90 else 'red'}]{exposure:.1f}%[/]",
        f"[bold]Unreal[/]\n[{_pnl_color(unrealized)}]${unrealized:+,.2f}[/]",
        f"[bold]Day P&L[/]\n[{_pnl_color(realized)}]${realized:+,.2f}[/]",
        f"[bold]Total[/]\n[{_pnl_color(total_pnl)}]${total_pnl:+,.2f}[/]",
        f"[bold]WR[/]\n[{'green' if total_wr >= 50 else 'red'}]{total_wr:.0f}%({total_trades})[/]",
        f"[bold]DD[/]\n[{'green' if drawdown < 5 else 'yellow' if drawdown < 10 else 'red'}]{drawdown:.1f}%[/]",
    )

    content = Group(grid)

    if positions:
        pos_table = Table(border_style="dim", show_header=True, header_style="bold", padding=(0, 1), expand=True)
        pos_table.add_column("Bot", style="bold cyan", max_width=6)
        pos_table.add_column("Asset", style="bold")
        pos_table.add_column("Qty", justify="right")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Current", justify="right")
        pos_table.add_column("Value", justify="right")
        pos_table.add_column("P&L", justify="right")
        pos_table.add_column("Alloc%", justify="right")

        for p in positions:
            pair = p.get("pair", p.get("symbol", "?"))
            bot_id = p.get("bot_id", "?")
            bot_style = "green" if bot_id == "swing" else "cyan" if bot_id == "day" else "dim"
            qty = p.get("qty", 0)
            entry = p.get("avg_entry_price", 0)
            current = p.get("current_price", 0)
            pnl = p.get("unrealized_pnl", p.get("unrealized_pl", 0))
            pnl_pct = float(p.get("unrealized_pnl_pct", 0))
            if pnl_pct == 0 and entry > 0 and qty > 0:
                pnl_pct = (pnl / (entry * qty) * 100)
            mv = p.get("market_value", p.get("market_value_usd", qty * current))
            alloc = (mv / nav * 100) if nav > 0 else 0

            pos_table.add_row(
                f"[{bot_style}]{bot_id}[/]",
                pair, f"{qty:.6f}",
                f"${entry:,.2f}", f"${current:,.2f}", f"${mv:,.2f}",
                f"[{_pnl_color(pnl)}]${pnl:+,.2f} ({pnl_pct:+.1f}%)[/]",
                f"[bright_yellow]{alloc:.1f}%[/]",
            )
        content = Group(grid, Text(""), pos_table)

    return Panel(content, title="📊 PORTFOLIO", title_align="left", border_style="magenta")


def build_prices_table(prices: dict, price_history: dict | None = None,
                       microstructure: dict | None = None) -> Panel:
    table = Table(border_style="blue", show_header=True, header_style="bold white", padding=(0, 1), expand=True)
    table.add_column("Pair", style="bold white", min_width=10)
    table.add_column("Mid", justify="right", style="bold")
    table.add_column("Spread", justify="right")
    table.add_column("Flow", justify="center")
    table.add_column("VPIN", justify="right")
    table.add_column("Chart", min_width=32)

    for pair, data in sorted(prices.items()):
        mid = data.get("mid", 0)
        bid = data.get("bid", 0)
        ask = data.get("ask", 0)
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 0

        history = (price_history or {}).get(pair, [])
        spark = _spark_line(history, width=30)

        if mid >= 1000:
            fmt = f"${mid:,.2f}"
        elif mid >= 1:
            fmt = f"${mid:,.4f}"
        else:
            fmt = f"${mid:,.6f}"

        spread_style = "green" if spread_bps < 20 else "yellow" if spread_bps < 50 else "red"

        micro = (microstructure or {}).get(pair, {})
        if hasattr(micro, "signal"):
            flow_sig = micro.signal
            vpin_val = micro.vpin
        else:
            flow_sig = micro.get("signal", "—") if isinstance(micro, dict) else "—"
            vpin_val = micro.get("vpin", 0) if isinstance(micro, dict) else 0

        flow_style = _signal_style(flow_sig)
        vpin_style = "red" if vpin_val > 0.7 else "yellow" if vpin_val > 0.5 else "dim"

        table.add_row(
            pair, fmt, f"[{spread_style}]{spread_bps:.1f}bps[/]",
            f"[{flow_style}]{flow_sig}[/]",
            f"[{vpin_style}]{vpin_val:.2f}[/]",
            f"[cyan]{spark}[/]" if spark else "—",
        )

    return Panel(table, title="💰 LIVE PRICES", title_align="left", border_style="blue")


def build_signals_panel(tech_signals: dict, fund_signals: dict, news_data: dict) -> Panel:
    table = Table(border_style="dim", show_header=True, header_style="bold", padding=(0, 1), expand=True)
    table.add_column("Pair", style="bold white")
    table.add_column("Tech", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Fund", justify="center")
    table.add_column("Score", justify="right")

    all_pairs = sorted(set(list(tech_signals.keys()) + list(fund_signals.keys())))
    for pair in all_pairs:
        t = tech_signals.get(pair, {})
        f = fund_signals.get(pair, {})
        table.add_row(
            pair,
            f"[{_signal_style(t.get('signal', '—'))}]{t.get('signal', '—')}[/]",
            f"{t.get('score', 0):+.2f}",
            f"[{_signal_style(f.get('signal', '—'))}]{f.get('signal', '—')}[/]",
            f"{f.get('score', 0):+.2f}",
        )

    news_sentiment = news_data.get("overall_sentiment", "—") if isinstance(news_data, dict) else "—"
    news_score = news_data.get("overall_score", 0) if isinstance(news_data, dict) else 0
    footer = Text(f"\n  📰 News: ", style="dim")
    footer.append(f"{news_sentiment}", style=_signal_style(news_sentiment))
    footer.append(f" ({news_score:+.2f})", style="dim")

    return Panel(Group(table, footer), title="📡 SIGNALS", title_align="left", border_style="green")


def build_strategy_panel(strategy_signals: dict, backtest: dict | None = None) -> Panel:
    lines: list[Text] = []

    for pair, strats in sorted(strategy_signals.items()):
        line = Text(f"  {pair}: ", style="bold")
        if isinstance(strats, list):
            buys = [s["name"] for s in strats if isinstance(s, dict) and s.get("signal") == "buy"]
            sells = [s["name"] for s in strats if isinstance(s, dict) and s.get("signal") == "sell"]
            for b in buys:
                line.append(f"▲{b} ", style="green")
            for s in sells:
                line.append(f"▼{s} ", style="red")
        elif isinstance(strats, dict):
            for b in strats.get("buy", []):
                line.append(f"▲{b} ", style="green")
            for s in strats.get("sell", []):
                line.append(f"▼{s} ", style="red")
        lines.append(line)

    if backtest and backtest.get("aggregate"):
        lines.append(Text(""))
        bt_line = Text("  Backtest: ", style="dim")
        for a in backtest["aggregate"]:
            s = a.get("sharpe", 0)
            col = "green" if s > 0.5 else "yellow" if s > 0 else "red"
            bt_line.append(
                f"{a['name']}(S={s:.1f},W={a.get('win_rate', 0) * 100:.0f}%,w={a.get('weight', 0) * 100:.0f}%) ",
                style=col,
            )
        lines.append(bt_line)

    return Panel(
        Group(*lines) if lines else Text("  Computing strategies...", style="dim"),
        title="🎯 STRATEGIES", title_align="left", border_style="bright_cyan",
    )


def build_thinking_panel(agent_log: list[dict]) -> Panel:
    icons = {
        "swing_sniper": "📈", "day_sniper": "⚡", "order_executor": "💰",
        "news_scout": "📰", "healer": "🩺",
    }
    agent_styles = {
        "swing_sniper": "green", "day_sniper": "cyan", "order_executor": "red",
        "news_scout": "yellow", "healer": "bright_yellow",
    }

    lines: list[Text] = []
    for entry in agent_log[-20:]:
        ts = str(entry.get("ts", ""))[11:19]
        agent = entry.get("agent", "?")
        step = entry.get("step", "")
        icon = icons.get(agent, "🤖")
        style = agent_styles.get(agent, "dim")

        line = Text()
        line.append(f"{ts} ", style="dim")
        line.append(f"{icon} {agent.replace('_', ' ')} ", style=f"bold {style}")
        line.append(step[:80])
        lines.append(line)

    return Panel(
        Group(*lines) if lines else Text("  Waiting for agent cycle...", style="dim"),
        title="🧠 AGENT THINKING", title_align="left", border_style="bright_green",
    )


def build_agent_status(agent_statuses: dict[str, str]) -> Panel:
    icons = {
        "swing_sniper": "📈", "day_sniper": "⚡", "order_executor": "💰",
        "news_scout": "📰",
    }
    status_icons = {
        "healthy": "🟢", "running": "🔵", "idle": "⚪", "standby": "🟣",
        "error": "🔴", "healing": "🟡", "circuit_open": "🔶",
    }

    parts = []
    for agent, status in agent_statuses.items():
        icon = icons.get(agent, "🤖")
        s_icon = status_icons.get(status, "⚪")
        style = ""
        if status == "error":
            style = "bold red"
        elif status == "healing":
            style = "bold yellow"
        parts.append(f" {icon} {agent}: {s_icon} [{style}]{status}[/]" if style else f" {icon} {agent}: {s_icon} {status}")

    return Panel(
        Text.from_markup("\n".join(parts)) if parts else Text("  No agent data", style="dim"),
        title="🤖 AGENTS", title_align="left", border_style="cyan",
    )


def build_trades_panel(recent_trades: list[dict]) -> Panel:
    if not recent_trades:
        return Panel(Text("  No trades yet", style="dim italic"), title="📋 TRADES", title_align="left", border_style="yellow")

    table = Table(border_style="dim", show_header=True, header_style="bold", padding=(0, 1), expand=True)
    table.add_column("Time", style="dim")
    table.add_column("Bot", max_width=6)
    table.add_column("Side", justify="center")
    table.add_column("Pair")
    table.add_column("Price", justify="right")
    table.add_column("P&L", justify="right")

    for t in recent_trades[-10:]:
        side = t.get("side", "?")
        side_style = "bold green" if side == "BUY" else "bold red"
        pnl = t.get("pnl", 0) or 0
        time_str = str(t.get("opened_at", ""))[:19]
        bot_id = t.get("bot_id", "?")
        bot_style = "green" if bot_id == "swing" else "cyan"
        table.add_row(
            time_str, f"[{bot_style}]{bot_id}[/]",
            f"[{side_style}]{side}[/]", t.get("pair", "?"),
            f"${t.get('entry_price', t.get('price', 0)):,.2f}",
            f"[{_pnl_color(pnl)}]${pnl:+,.2f}[/]",
        )

    return Panel(table, title="📋 TRADES", title_align="left", border_style="yellow")


def build_healing_panel(healing_events: list[dict]) -> Panel | None:
    if not healing_events:
        return None

    table = Table(border_style="dim", show_header=True, header_style="bold", padding=(0, 1), expand=True)
    table.add_column("Time", style="dim", max_width=8)
    table.add_column("Agent", max_width=14)
    table.add_column("", max_width=3)
    table.add_column("Event", ratio=1)

    outcome_icons = {"healed": "✅", "retrying": "🔄", "circuit_open": "🔶", "skipped": "⏭️", "retry_failed": "❌"}
    severity_style = {"critical": "bold red", "warning": "yellow", "transient": "cyan", "info": "green"}

    for evt in healing_events[-6:]:
        ts = str(evt.get("timestamp", ""))
        time_str = ts[11:19] if len(ts) > 19 else ts[:8]
        table.add_row(
            time_str, evt.get("agent", "?"),
            outcome_icons.get(evt.get("outcome", ""), ""),
            f"[{severity_style.get(evt.get('severity', 'info'), 'dim')}]{evt.get('message', '')[:50]}[/]",
        )

    return Panel(table, title="🩺 SELF-HEALING", title_align="left", border_style="bright_yellow")


def build_full_display(
    prices: dict,
    portfolio: dict,
    positions: list,
    tech_signals: dict,
    fund_signals: dict,
    news_data: dict,
    recent_trades: list,
    agent_statuses: dict,
    price_history: dict | None = None,
    healing_events: list | None = None,
    mode: str = "LIVE",
    uptime_sec: int = 0,
    regime: dict | None = None,
    exchange_status: str = "checking",
    onchain: dict | None = None,
    microstructure: dict | None = None,
    strategy_signals: dict | None = None,
    backtest: dict | None = None,
    agent_log: list | None = None,
) -> Group:
    """Build the complete Bloomberg-style terminal display."""
    parts = [
        build_header(mode, uptime_sec, regime, exchange_status, onchain),
        build_portfolio_panel(portfolio, positions),
    ]

    col_left = Group(
        build_prices_table(prices, price_history, microstructure),
        build_signals_panel(tech_signals, fund_signals, news_data),
    )

    col_right = Group(
        build_strategy_panel(strategy_signals or {}, backtest),
        build_thinking_panel(agent_log or []),
    )

    parts.append(Columns([col_left, col_right], expand=True, equal=True))

    parts.append(Columns([
        build_trades_panel(recent_trades),
        build_agent_status(agent_statuses),
    ], expand=True, equal=True))

    healing_panel = build_healing_panel(healing_events or [])
    if healing_panel:
        parts.append(healing_panel)

    return Group(*parts)
