"""Rich terminal UI for Alpha-Paca Crypto — live prices, portfolio, signals, charts."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

SPARK_CHARS = "▁▂▃▄▅▆▇█"
BULL_COLOR = "green"
BEAR_COLOR = "red"
NEUTRAL_COLOR = "yellow"


def _spark_line(values: list[float], width: int = 30) -> str:
    """Generate a sparkline string from a list of values."""
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
    if "strong_buy" in s:
        return "bold green"
    if "buy" in s:
        return "green"
    if "strong_sell" in s:
        return "bold red"
    if "sell" in s:
        return "red"
    return "yellow"


def build_header(mode: str = "PAPER", uptime_sec: int = 0) -> Panel:
    hrs = uptime_sec // 3600
    mins = (uptime_sec % 3600) // 60
    secs = uptime_sec % 60
    mode_style = "bold green" if mode == "PAPER" else "bold red"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = Text()
    header.append("  ╔═══════════════════════════════════════════════════╗\n", style="cyan")
    header.append("  ║  ", style="cyan")
    header.append("🦙 ALPHA-PACA CRYPTO", style="bold white")
    header.append("  ║\n", style="cyan")
    header.append("  ╚═══════════════════════════════════════════════════╝\n", style="cyan")
    header.append(f"  Mode: ", style="dim")
    header.append(f"{mode}", style=mode_style)
    header.append(f"  │  Uptime: {hrs:02d}:{mins:02d}:{secs:02d}", style="dim")
    header.append(f"  │  {now}", style="dim")
    return Panel(header, border_style="cyan", padding=(0, 1))


def build_prices_table(prices: dict[str, dict], price_history: dict[str, list[float]] | None = None) -> Panel:
    table = Table(
        title="💰 LIVE PRICES",
        title_style="bold cyan",
        border_style="blue",
        show_header=True,
        header_style="bold white",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Pair", style="bold white", min_width=10)
    table.add_column("Bid", justify="right", style="dim")
    table.add_column("Ask", justify="right", style="dim")
    table.add_column("Mid", justify="right", style="bold")
    table.add_column("Spread", justify="right")
    table.add_column("Chart (30 ticks)", min_width=32)

    for pair, data in sorted(prices.items()):
        bid = data.get("bid", 0)
        ask = data.get("ask", 0)
        mid = data.get("mid", 0)
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 0

        history = (price_history or {}).get(pair, [])
        spark = _spark_line(history, width=30) if history else "—"

        if mid >= 1000:
            fmt = f"${mid:,.2f}"
            bid_fmt = f"${bid:,.2f}"
            ask_fmt = f"${ask:,.2f}"
        elif mid >= 1:
            fmt = f"${mid:,.4f}"
            bid_fmt = f"${bid:,.4f}"
            ask_fmt = f"${ask:,.4f}"
        else:
            fmt = f"${mid:,.6f}"
            bid_fmt = f"${bid:,.6f}"
            ask_fmt = f"${ask:,.6f}"

        spread_style = "green" if spread_bps < 20 else "yellow" if spread_bps < 50 else "red"
        table.add_row(pair, bid_fmt, ask_fmt, fmt, f"[{spread_style}]{spread_bps:.1f}bps[/]", spark)

    return Panel(table, border_style="blue")


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
    daily_trades = portfolio.get("daily_trades", 0)
    daily_wr = portfolio.get("daily_win_rate", 0)

    grid = Table.grid(padding=(0, 2), expand=True)
    for _ in range(8):
        grid.add_column(justify="center")

    grid.add_row(
        f"[bold]NAV[/]\n[white]${nav:,.2f}[/]",
        f"[bold]Cash[/]\n[white]${cash:,.2f}[/]",
        f"[bold]Exposure[/]\n[{'green' if exposure < 70 else 'yellow' if exposure < 90 else 'red'}]{exposure:.1f}%[/]",
        f"[bold]Unrealized[/]\n[{_pnl_color(unrealized)}]${unrealized:+,.2f}[/]",
        f"[bold]Day P&L[/]\n[{_pnl_color(realized)}]${realized:+,.2f}[/]",
        f"[bold]Total P&L[/]\n[{_pnl_color(total_pnl)}]${total_pnl:+,.2f}[/]",
        f"[bold]Win Rate[/]\n[{'green' if total_wr >= 50 else 'red'}]{total_wr:.0f}% ({total_trades})[/]",
        f"[bold]Drawdown[/]\n[{'green' if drawdown < 5 else 'yellow' if drawdown < 10 else 'red'}]{drawdown:.1f}%[/]",
    )

    content = Group(grid)

    if positions:
        pos_table = Table(
            border_style="dim", show_header=True, header_style="bold",
            padding=(0, 1), expand=True,
        )
        pos_table.add_column("Pair", style="bold")
        pos_table.add_column("Side", justify="center")
        pos_table.add_column("Qty", justify="right")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Current", justify="right")
        pos_table.add_column("P&L", justify="right")
        pos_table.add_column("P&L %", justify="right")
        pos_table.add_column("Value", justify="right")

        for p in positions:
            pair = p.get("pair", p.get("symbol", "?"))
            side = p.get("side", "long").upper()
            side_style = "green" if side == "LONG" else "red"
            qty = p.get("qty", 0)
            entry = p.get("avg_entry_price", 0)
            current = p.get("current_price", 0)
            pnl = p.get("unrealized_pnl", p.get("unrealized_pl", 0))
            pnl_pct = float(p.get("unrealized_pnl_pct", 0))
            if pnl_pct == 0 and entry > 0 and qty > 0:
                pnl_pct = (pnl / (entry * qty) * 100)
            mv = p.get("market_value", p.get("market_value_usd", qty * current))

            pos_table.add_row(
                pair,
                f"[{side_style}]{side}[/]",
                f"{qty:.6f}",
                f"${entry:,.2f}",
                f"${current:,.2f}",
                f"[{_pnl_color(pnl)}]${pnl:+,.2f}[/]",
                f"[{_pnl_color(pnl_pct)}]{pnl_pct:+.2f}%[/]",
                f"${mv:,.2f}",
            )
        content = Group(grid, Text(""), pos_table)
    else:
        content = Group(grid, Text("\n  No open positions", style="dim italic"))

    return Panel(content, title="📊 PORTFOLIO", title_align="left", border_style="magenta")


def build_signals_panel(tech_signals: dict, fund_signals: dict, news_data: dict) -> Panel:
    table = Table(
        border_style="dim", show_header=True, header_style="bold",
        padding=(0, 1), expand=True,
    )
    table.add_column("Pair", style="bold white")
    table.add_column("Technical", justify="center")
    table.add_column("T.Score", justify="right")
    table.add_column("Fundamental", justify="center")
    table.add_column("F.Score", justify="right")
    table.add_column("Details")

    all_pairs = sorted(set(list(tech_signals.keys()) + list(fund_signals.keys())))
    for pair in all_pairs:
        t = tech_signals.get(pair, {})
        f = fund_signals.get(pair, {})
        t_sig = t.get("signal", "—")
        t_score = t.get("score", 0)
        f_sig = f.get("signal", "—")
        f_score = f.get("score", 0)
        details = t.get("details", "")[:40]

        table.add_row(
            pair,
            f"[{_signal_style(t_sig)}]{t_sig}[/]",
            f"{t_score:+.2f}",
            f"[{_signal_style(f_sig)}]{f_sig}[/]",
            f"{f_score:+.2f}",
            f"[dim]{details}[/]",
        )

    news_sentiment = news_data.get("overall_sentiment", "—") if isinstance(news_data, dict) else "—"
    news_score = news_data.get("overall_score", 0) if isinstance(news_data, dict) else 0

    footer = Text(f"\n  📰 News Sentiment: ", style="dim")
    footer.append(f"{news_sentiment}", style=_signal_style(news_sentiment))
    footer.append(f" (score: {news_score:+.2f})", style="dim")

    return Panel(
        Group(table, footer),
        title="📡 SIGNALS", title_align="left", border_style="green",
    )


def build_trades_panel(recent_trades: list[dict]) -> Panel:
    if not recent_trades:
        return Panel(
            Text("  No trades yet", style="dim italic"),
            title="📋 RECENT TRADES", title_align="left", border_style="yellow",
        )

    table = Table(
        border_style="dim", show_header=True, header_style="bold",
        padding=(0, 1), expand=True,
    )
    table.add_column("Time", style="dim")
    table.add_column("Side", justify="center")
    table.add_column("Pair")
    table.add_column("Qty", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Reason")

    for t in recent_trades[-8:]:
        side = t.get("side", "?")
        side_style = "bold green" if side == "BUY" else "bold red"
        pnl = t.get("pnl", 0) or 0
        time_str = str(t.get("opened_at", ""))[:19]

        table.add_row(
            time_str,
            f"[{side_style}]{side}[/]",
            t.get("pair", "?"),
            f"{t.get('qty', 0):.6f}",
            f"${t.get('entry_price', t.get('price', 0)):,.2f}",
            f"[{_pnl_color(pnl)}]${pnl:+,.2f}[/]",
            f"[dim]{(t.get('reasoning', '') or '')[:30]}[/]",
        )

    return Panel(table, title="📋 RECENT TRADES", title_align="left", border_style="yellow")


def build_agent_status(agent_statuses: dict[str, str]) -> Panel:
    icons = {
        "news_scout": "📰",
        "technical_analyst": "📈",
        "fundamental_analyst": "🔬",
        "orchestrator": "🧠",
        "risk_validator": "🛡️",
        "order_executor": "⚡",
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
        elif status == "circuit_open":
            style = "bold bright_red"
        parts.append(f" {icon} {agent}: {s_icon} [{style}]{status}[/]" if style else f" {icon} {agent}: {s_icon} {status}")

    return Panel(
        Text.from_markup("\n".join(parts)) if parts else Text("  No agent data", style="dim"),
        title="🤖 AGENTS", title_align="left", border_style="cyan",
    )


def build_healing_panel(healing_events: list[dict]) -> Panel | None:
    """Build a panel showing recent self-healing activity."""
    if not healing_events:
        return None

    severity_style = {
        "critical": "bold red",
        "warning": "yellow",
        "transient": "cyan",
        "info": "green",
    }

    outcome_icons = {
        "healed": "✅",
        "retrying": "🔄",
        "circuit_open": "🔶",
        "skipped": "⏭️",
        "retry_failed": "❌",
    }

    table = Table(
        border_style="dim", show_header=True, header_style="bold",
        padding=(0, 1), expand=True,
    )
    table.add_column("Time", style="dim", max_width=8)
    table.add_column("Agent", max_width=14)
    table.add_column("", max_width=3)
    table.add_column("Event", ratio=1)

    for evt in healing_events[-6:]:
        ts = str(evt.get("timestamp", ""))
        time_str = ts[11:19] if len(ts) > 19 else ts[:8]
        agent = evt.get("agent", "?")
        severity = evt.get("severity", "info")
        outcome = evt.get("outcome", "")
        message = evt.get("message", "")[:50]
        o_icon = outcome_icons.get(outcome, "")
        s_style = severity_style.get(severity, "dim")

        table.add_row(time_str, agent, o_icon, f"[{s_style}]{message}[/]")

    return Panel(
        table,
        title="🩺 SELF-HEALING", title_align="left", border_style="bright_yellow",
    )


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
    mode: str = "PAPER",
    uptime_sec: int = 0,
) -> Group:
    """Build the complete terminal display."""
    parts = [
        build_header(mode, uptime_sec),
        build_prices_table(prices, price_history),
        build_portfolio_panel(portfolio, positions),
        build_signals_panel(tech_signals, fund_signals, news_data),
        Columns([
            build_trades_panel(recent_trades),
            build_agent_status(agent_statuses),
        ], expand=True, equal=True),
    ]

    healing_panel = build_healing_panel(healing_events or [])
    if healing_panel:
        parts.append(healing_panel)

    return Group(*parts)
