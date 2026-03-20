"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface BacktestResult {
  id: string;
  pod_name: string;
  calculated_at: string;
  sharpe_ratio: number | null;
  total_return: number | null;
  max_drawdown: number | null;
  win_rate: number | null;
  num_trades: number | null;
  status: string;
  [key: string]: unknown;
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "—";
  return n.toFixed(d);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

export default function BacktestPage() {
  const [results, setResults] = useState<BacktestResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAPI<BacktestResult[]>("/backtest/results")
      .then(setResults)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load backtest results"),
      )
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Backtest Results</h1>

      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}

      {results.length === 0 ? (
        <p className="py-12 text-center text-muted-foreground">
          No backtest results available
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {results.map((r) => (
            <Card key={r.id}>
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-semibold">
                    {r.pod_name}
                  </CardTitle>
                  <Badge
                    variant={
                      r.status === "passed"
                        ? "profit"
                        : r.status === "failed"
                          ? "loss"
                          : "secondary"
                    }
                  >
                    {r.status}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {new Date(r.calculated_at).toLocaleString()}
                </p>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Sharpe
                    </p>
                    <p className="text-sm font-bold tabular-nums">
                      {fmt(r.sharpe_ratio)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Return
                    </p>
                    <p
                      className={`text-sm font-bold tabular-nums ${
                        (r.total_return ?? 0) >= 0 ? "text-profit" : "text-loss"
                      }`}
                    >
                      {fmtPct(r.total_return)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Max DD
                    </p>
                    <p className="text-sm font-bold tabular-nums text-loss">
                      {fmtPct(r.max_drawdown)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Win Rate
                    </p>
                    <p className="text-sm font-bold tabular-nums">
                      {fmtPct(r.win_rate)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Trades
                    </p>
                    <p className="text-sm font-bold tabular-nums">
                      {r.num_trades ?? "—"}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
