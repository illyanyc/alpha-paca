"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface PortfolioState {
  id: string;
  total_equity: number;
  cash: number;
  margin_used: number;
  unrealized_pnl: number;
  realized_pnl: number;
  open_positions: number;
  updated_at: string;
  [key: string]: unknown;
}

interface TradeStats {
  total_trades: number;
  closed_trades: number;
  win_rate: number | null;
  avg_pnl: number;
  wins: number;
}

interface RiskEvent {
  id: string;
  severity: string;
  message: string;
  created_at: string;
  [key: string]: unknown;
}

interface DrawdownState {
  current_drawdown: number;
  max_drawdown: number;
  peak_equity: number;
  updated_at: string;
  [key: string]: unknown;
}

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

function pnlColor(n: number | null | undefined): string {
  if (n == null || n === 0) return "text-muted-foreground";
  return n > 0 ? "text-profit" : "text-loss";
}

export default function OverviewPage() {
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [drawdown, setDrawdown] = useState<DrawdownState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [p, s, e, d] = await Promise.allSettled([
          fetchAPI<PortfolioState>("/portfolio/state"),
          fetchAPI<TradeStats>("/trades/stats"),
          fetchAPI<RiskEvent[]>("/risk/events?limit=5"),
          fetchAPI<DrawdownState>("/risk/drawdown"),
        ]);
        if (p.status === "fulfilled") setPortfolio(p.value);
        if (s.status === "fulfilled") setStats(s.value);
        if (e.status === "fulfilled") setEvents(e.value);
        if (d.status === "fulfilled") setDrawdown(d.value);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-96 items-center justify-center">
        <p className="text-loss">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Risk Overview</h1>

      {/* Portfolio Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">
              Total Equity
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p className="text-2xl font-bold tabular-nums">
              ${fmt(portfolio?.total_equity)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">
              Unrealized P&L
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p
              className={`text-2xl font-bold tabular-nums ${pnlColor(portfolio?.unrealized_pnl)}`}
            >
              ${fmt(portfolio?.unrealized_pnl)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">
              Realized P&L
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p
              className={`text-2xl font-bold tabular-nums ${pnlColor(portfolio?.realized_pnl)}`}
            >
              ${fmt(portfolio?.realized_pnl)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">
              Open Positions
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p className="text-2xl font-bold tabular-nums">
              {portfolio?.open_positions ?? "—"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Trade Stats + Drawdown Row */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-semibold">Trade Statistics</CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Total Trades</p>
                <p className="text-lg font-bold tabular-nums">
                  {stats?.total_trades ?? "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Win Rate</p>
                <p className="text-lg font-bold tabular-nums text-profit">
                  {fmtPct(stats?.win_rate)}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Avg P&L</p>
                <p
                  className={`text-lg font-bold tabular-nums ${pnlColor(stats?.avg_pnl)}`}
                >
                  ${fmt(stats?.avg_pnl)}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Wins / Closed</p>
                <p className="text-lg font-bold tabular-nums">
                  {stats?.wins ?? 0} / {stats?.closed_trades ?? 0}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-semibold">Drawdown</CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Current DD</p>
                <p className="text-lg font-bold tabular-nums text-loss">
                  {fmtPct(drawdown?.current_drawdown)}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Max DD</p>
                <p className="text-lg font-bold tabular-nums text-loss">
                  {fmtPct(drawdown?.max_drawdown)}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Peak Equity</p>
                <p className="text-lg font-bold tabular-nums">
                  ${fmt(drawdown?.peak_equity)}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Cash</p>
                <p className="text-lg font-bold tabular-nums">
                  ${fmt(portfolio?.cash)}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Recent Risk Events */}
      <Card>
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-sm font-semibold">Recent Risk Events</CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recent events</p>
          ) : (
            <div className="space-y-2">
              {events.map((ev) => (
                <div
                  key={ev.id}
                  className="flex items-start gap-3 rounded-md border border-border/50 p-3"
                >
                  <Badge
                    variant={
                      ev.severity === "critical"
                        ? "loss"
                        : ev.severity === "warning"
                          ? "warning"
                          : "secondary"
                    }
                  >
                    {ev.severity}
                  </Badge>
                  <div className="flex-1">
                    <p className="text-sm">{ev.message}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {new Date(ev.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
