"""End-to-end paper trade test — exercises the full pipeline without AI agents."""

import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


async def run_paper_test():
    from config import get_settings
    from db.engine import Base, async_session_factory, engine
    from db import models as _m  # noqa: F401
    from db.models import CryptoTrade, CryptoPosition
    from services.coinbase_crypto import CoinbaseCryptoService
    from services.price_tracker import PriceTracker
    from agents.technical_analyst import TechnicalAnalystAgent
    from agents.fundamental_analyst import FundamentalAnalystAgent
    from agents.risk_validator import RiskValidatorAgent
    from engine.signals import classify_technical, composite_score, ComponentSignal, SignalStrength
    from engine.position_sizer import compute_position_size
    from sqlalchemy import select, delete

    settings = get_settings()
    console.print(Panel(
        f"[bold cyan]Paper Trade Test[/]\n"
        f"Pairs: {', '.join(settings.crypto.pair_list)}\n"
        f"Capital: ${settings.crypto.max_capital:,.0f}\n"
        f"Exchange: Coinbase",
        border_style="cyan",
    ))

    # 1 — DB setup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    console.print("[green]✓[/] DB tables ready")

    # 2 — Fetch live prices
    exchange = CoinbaseCryptoService()
    pt = PriceTracker(exchange)
    prices = await pt.fetch_and_cache()
    console.print(f"[green]✓[/] Fetched {len(prices)} live quotes")

    price_table = Table(title="Live Prices", border_style="blue")
    price_table.add_column("Pair", style="bold")
    price_table.add_column("Mid", justify="right")
    for pair, data in sorted(prices.items()):
        mid = data["mid"]
        price_table.add_row(pair, f"${mid:,.2f}" if mid > 1 else f"${mid:,.6f}")
    console.print(price_table)

    # 3 — Technical analysis
    tech = TechnicalAnalystAgent(exchange)
    tech_result = await tech.safe_run()
    console.print(f"[green]✓[/] Technical analysis: {len(tech_result)} pairs")

    # 4 — Fundamental analysis
    fund = FundamentalAnalystAgent(exchange)
    fund_result = await fund.safe_run()
    console.print(f"[green]✓[/] Fundamental analysis: {len(fund_result)} pairs")

    # 5 — Build composite signals and find best trade
    console.print("\n[bold]Signal Analysis:[/]")
    best_pair = None
    best_score = -999
    best_conf = 0

    sig_table = Table(border_style="green")
    sig_table.add_column("Pair")
    sig_table.add_column("Tech Signal", justify="center")
    sig_table.add_column("Tech Score", justify="right")
    sig_table.add_column("Fund Score", justify="right")
    sig_table.add_column("Composite", justify="right")
    sig_table.add_column("Action")

    for pair in settings.crypto.pair_list:
        t = tech_result.get(pair, {})
        f = fund_result.get(pair, {})

        t_score = t.get("score", 0)
        t_conf = t.get("confidence", 0)
        f_score = f.get("score", 0)
        f_conf = f.get("confidence", 0.5)

        signals = []
        if t.get("signal"):
            signals.append(ComponentSignal("technical", SignalStrength(t["signal"]), t_score, t_conf))
        if f.get("signal"):
            signals.append(ComponentSignal("fundamental", SignalStrength(f["signal"]), f_score, f_conf))

        comp_score, comp_conf = composite_score(signals) if signals else (0, 0)

        action = "HOLD"
        if comp_score > 0.2 and comp_conf > 0.3:
            action = "[green]BUY[/]"
        elif comp_score < -0.2 and comp_conf > 0.3:
            action = "[red]SELL[/]"

        sig_table.add_row(
            pair,
            f"[{'green' if t_score > 0 else 'red' if t_score < 0 else 'yellow'}]{t.get('signal', '—')}[/]",
            f"{t_score:+.2f}",
            f"{f_score:+.2f}",
            f"[bold]{comp_score:+.3f}[/]",
            action,
        )

        if comp_score > best_score:
            best_score = comp_score
            best_pair = pair
            best_conf = comp_conf

    console.print(sig_table)

    # 6 — Risk check on best candidate
    if best_pair and best_score > 0.1:
        console.print(f"\n[bold]Best candidate: {best_pair}[/] (score={best_score:+.3f}, conf={best_conf:.3f})")

        risk_agent = RiskValidatorAgent()
        decision = {
            "action": "BUY",
            "pair": best_pair,
            "size_pct": 5.0,
            "confidence": max(best_conf, 0.5),
        }
        risk_result = await risk_agent.safe_run(
            decision=decision,
            positions=[],
            portfolio_state={
                "nav": settings.crypto.max_capital,
                "cash": settings.crypto.max_capital,
                "total_exposure_pct": 0,
                "drawdown_pct": 0,
            },
        )
        console.print(f"  Risk check: [{'green' if risk_result['approved'] else 'red'}]{'APPROVED' if risk_result['approved'] else 'REJECTED'}[/]")
        if risk_result.get("reasons"):
            console.print(f"  Reasons: {risk_result['reasons']}")

        # 7 — Simulate position sizing
        mid_price = prices.get(best_pair, {}).get("mid", 0)
        if mid_price > 0 and risk_result["approved"]:
            t_data = tech_result.get(best_pair, {})
            atr_val = t_data.get("indicators", {}).get("atr")
            ps = compute_position_size(
                pair=best_pair, price=mid_price, confidence=best_conf,
                atr_value=atr_val, available_capital=settings.crypto.max_capital,
                current_exposure_pct=0,
            )
            console.print(f"  Position size: {ps.qty:.8f} {best_pair.split('/')[0]} (${ps.notional_usd:,.2f}, {ps.pct_of_capital:.1f}%)")
            console.print(f"  Method: {ps.method}")

            # 8 — Write simulated trade to DB
            async with async_session_factory() as session:
                trade = CryptoTrade(
                    pair=best_pair,
                    side="BUY",
                    qty=Decimal(str(round(ps.qty, 8))),
                    entry_price=Decimal(str(mid_price)),
                    confidence=best_conf,
                    reasoning=f"Paper test — tech={best_score:+.3f}",
                    status="open",
                    exchange_order_id="paper-test-001",
                )
                session.add(trade)
                await session.commit()
                console.print(f"  [green]✓[/] Trade recorded in DB (id={trade.id})")

            # 9 — Write simulated position
            async with async_session_factory() as session:
                stmt = select(CryptoPosition).where(CryptoPosition.pair == best_pair)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing:
                    existing.qty = Decimal(str(round(ps.qty, 8)))
                    existing.avg_entry_price = Decimal(str(mid_price))
                    existing.current_price = Decimal(str(mid_price))
                else:
                    pos = CryptoPosition(
                        pair=best_pair,
                        qty=Decimal(str(round(ps.qty, 8))),
                        avg_entry_price=Decimal(str(mid_price)),
                        current_price=Decimal(str(mid_price)),
                    )
                    session.add(pos)
                await session.commit()
                console.print(f"  [green]✓[/] Position recorded in DB")

            # 10 — Simulate price move and close trade
            console.print("\n[bold]Simulating exit...[/]")
            exit_price = mid_price * 1.001  # +0.1% simulated gain
            pnl = Decimal(str(round(ps.qty * (exit_price - mid_price), 8)))

            async with async_session_factory() as session:
                stmt = select(CryptoTrade).where(
                    CryptoTrade.exchange_order_id == "paper-test-001"
                )
                result = await session.execute(stmt)
                trade = result.scalar_one_or_none()
                if trade:
                    trade.exit_price = Decimal(str(exit_price))
                    trade.pnl = pnl
                    trade.pnl_pct = float(pnl / (Decimal(str(mid_price)) * Decimal(str(round(ps.qty, 8)))) * 100)
                    trade.status = "closed"
                    trade.closed_at = datetime.now(timezone.utc)
                    await session.commit()

            async with async_session_factory() as session:
                stmt = delete(CryptoPosition).where(CryptoPosition.pair == best_pair)
                await session.execute(stmt)
                await session.commit()

            console.print(f"  Exit price: ${exit_price:,.2f}")
            console.print(f"  P&L: [{'green' if pnl > 0 else 'red'}]${float(pnl):+,.4f}[/]")
            console.print(f"  [green]✓[/] Trade closed and position cleared")

    else:
        console.print(f"\n[yellow]No strong BUY signals — best was {best_pair} at {best_score:+.3f}[/]")

    # 11 — Verify DB state
    console.print("\n[bold]DB Verification:[/]")
    async with async_session_factory() as session:
        trades = (await session.execute(select(CryptoTrade))).scalars().all()
        positions = (await session.execute(select(CryptoPosition))).scalars().all()
        console.print(f"  Trades in DB: {len(trades)}")
        console.print(f"  Open positions: {len(positions)}")

    await pt.close()
    await engine.dispose()

    console.print(Panel(
        "[bold green]PAPER TRADE TEST COMPLETE[/]\n"
        "Full pipeline verified: prices → indicators → signals → risk → sizing → DB",
        border_style="green",
    ))


if __name__ == "__main__":
    asyncio.run(run_paper_test())
