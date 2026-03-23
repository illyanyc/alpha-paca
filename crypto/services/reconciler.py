"""Coinbase fill reconciliation — computes real PnL from exchange fills.

Replaces Redis-accumulated PnL (which can accumulate phantom trades)
with ground-truth data from actual Coinbase fill records.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import structlog

from services.coinbase_crypto import CoinbaseCryptoService

logger = structlog.get_logger(__name__)

RECONCILE_REDIS_KEY = "alphapaca:crypto:settings:pnl:reconciled"


def compute_pnl_from_fills(
    fills: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute per-pair PnL and total fees from raw Coinbase fills.

    Uses FIFO cost basis per pair: buys build a cost queue,
    sells dequeue and realize PnL.
    """
    pair_buys: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pair_stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "buy_qty": 0.0,
            "sell_qty": 0.0,
            "fees": 0.0,
            "realized_pnl": 0.0,
            "buy_count": 0,
            "sell_count": 0,
        }
    )

    sorted_fills = sorted(
        fills,
        key=lambda f: f.get("trade_time", f.get("sequence_timestamp", "")),
    )

    for fill in sorted_fills:
        product_id = fill.get("product_id", "")
        pair = product_id.replace("-", "/")
        side = fill.get("side", "").upper()
        qty = float(fill.get("size", 0))
        price = float(fill.get("price", 0))
        commission = float(fill.get("commission", 0))
        notional = qty * price

        if qty <= 0 or price <= 0:
            continue

        stats = pair_stats[pair]
        stats["fees"] += commission

        if side == "BUY":
            pair_buys[pair].append((qty, price))
            stats["buy_volume"] += notional
            stats["buy_qty"] += qty
            stats["buy_count"] += 1
        elif side == "SELL":
            stats["sell_volume"] += notional
            stats["sell_qty"] += qty
            stats["sell_count"] += 1

            remaining = qty
            proceeds = notional
            cost = 0.0
            buy_queue = pair_buys[pair]
            while remaining > 0 and buy_queue:
                bq, bp = buy_queue[0]
                used = min(bq, remaining)
                cost += used * bp
                remaining -= used
                if used >= bq:
                    buy_queue.pop(0)
                else:
                    buy_queue[0] = (bq - used, bp)

            stats["realized_pnl"] += (proceeds - cost)

    total_realized = sum(s["realized_pnl"] for s in pair_stats.values())
    total_fees = sum(s["fees"] for s in pair_stats.values())
    total_buy_volume = sum(s["buy_volume"] for s in pair_stats.values())
    total_sell_volume = sum(s["sell_volume"] for s in pair_stats.values())
    total_buys = sum(s["buy_count"] for s in pair_stats.values())
    total_sells = sum(s["sell_count"] for s in pair_stats.values())

    remaining_holdings: dict[str, float] = {}
    for pair, queue in pair_buys.items():
        held_qty = sum(q for q, _ in queue)
        if held_qty > 0.0001:
            remaining_holdings[pair] = held_qty

    win_count = sum(1 for s in pair_stats.values() if s["realized_pnl"] > 0)
    loss_count = sum(1 for s in pair_stats.values() if s["realized_pnl"] <= 0 and s["sell_count"] > 0)
    trade_count = total_sells

    return {
        "total_realized_pnl": round(total_realized, 4),
        "total_fees": round(total_fees, 4),
        "net_pnl": round(total_realized - total_fees, 4),
        "total_trades": total_buys + total_sells,
        "total_sells": total_sells,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_count / (win_count + loss_count) * 100, 1) if (win_count + loss_count) > 0 else 0,
        "buy_volume": round(total_buy_volume, 2),
        "sell_volume": round(total_sell_volume, 2),
        "per_pair": {
            pair: {
                "pnl": round(s["realized_pnl"], 4),
                "fees": round(s["fees"], 4),
                "trades": s["buy_count"] + s["sell_count"],
                "wins": 1 if s["realized_pnl"] > 0 else 0,
            }
            for pair, s in pair_stats.items()
        },
        "remaining_holdings": remaining_holdings,
    }


async def reconcile_pnl(
    exchange: CoinbaseCryptoService,
    redis_conn: Any,
    start_date: str | None = None,
    tracked_pairs: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch all fills from Coinbase and overwrite Redis PnL with truth.

    Args:
        tracked_pairs: Only include fills for these pairs (e.g. ["BTC/USD", "ETH/USD"]).
                       If None, uses pair_list from settings.
    """
    import asyncio
    from config import get_settings

    if tracked_pairs is None:
        tracked_pairs = get_settings().crypto.pair_list

    tracked_product_ids = {p.replace("/", "-") for p in tracked_pairs}

    logger.info("reconciliation_started", pairs=tracked_pairs)

    fills = await asyncio.to_thread(
        exchange.get_all_fills, start_date=start_date
    )

    fills = [
        f for f in fills
        if f.get("product_id", "") in tracked_product_ids
    ]

    if not fills:
        logger.warning("reconciliation_no_fills")
        return {"error": "no fills returned from Coinbase"}

    logger.info("reconciliation_fills_fetched", count=len(fills))

    result = compute_pnl_from_fills(fills)

    from services.settings_store import (
        PNL_PER_PAIR_KEY,
        PNL_TOTAL_KEY,
    )

    pipe = redis_conn.pipeline()

    pipe.delete(PNL_TOTAL_KEY)
    pipe.hset(PNL_TOTAL_KEY, "realized_pnl", str(result["net_pnl"]))
    pipe.hset(PNL_TOTAL_KEY, "trade_count", str(result["total_sells"]))
    pipe.hset(PNL_TOTAL_KEY, "win_count", str(result["win_count"]))
    pipe.hset(PNL_TOTAL_KEY, "fees", str(result["total_fees"]))

    pipe.delete(PNL_PER_PAIR_KEY)
    for pair, pair_data in result["per_pair"].items():
        net_pair_pnl = pair_data["pnl"] - pair_data["fees"]
        pipe.hset(PNL_PER_PAIR_KEY, f"{pair}:pnl", str(net_pair_pnl))
        pipe.hset(PNL_PER_PAIR_KEY, f"{pair}:trades", str(pair_data["trades"]))
        pipe.hset(PNL_PER_PAIR_KEY, f"{pair}:wins", str(pair_data["wins"]))

    pipe.set(
        RECONCILE_REDIS_KEY,
        datetime.now(timezone.utc).isoformat(),
        ex=86400 * 7,
    )

    await pipe.execute()

    logger.info(
        "reconciliation_complete",
        total_pnl=result["net_pnl"],
        fees=result["total_fees"],
        trading_pnl=result["total_realized_pnl"],
        fills=len(fills),
        pairs=len(result["per_pair"]),
    )

    return result
