"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface PodAllocation {
  id: string;
  pod_name: string;
  strategy_type: string;
  allocation_pct: number;
  status: string;
  sharpe_ratio: number | null;
  total_pnl: number | null;
  open_positions: number | null;
  [key: string]: unknown;
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

export default function PodsPage() {
  const [pods, setPods] = useState<PodAllocation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAPI<PodAllocation[]>("/pods/")
      .then(setPods)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load pods"),
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
      <h1 className="text-2xl font-bold tracking-tight">Pods</h1>

      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}

      {pods.length === 0 ? (
        <p className="py-12 text-center text-muted-foreground">
          No pod allocations configured
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {pods.map((pod) => (
            <Card key={pod.id}>
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-semibold">
                    {pod.pod_name}
                  </CardTitle>
                  <Badge
                    variant={
                      pod.status === "active"
                        ? "profit"
                        : pod.status === "paused"
                          ? "warning"
                          : "secondary"
                    }
                  >
                    {pod.status}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {pod.strategy_type}
                </p>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Allocation
                    </p>
                    <p className="text-sm font-bold tabular-nums">
                      {fmtPct(pod.allocation_pct)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Sharpe
                    </p>
                    <p className="text-sm font-bold tabular-nums">
                      {fmt(pod.sharpe_ratio)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Total P&L
                    </p>
                    <p
                      className={`text-sm font-bold tabular-nums ${
                        (pod.total_pnl ?? 0) >= 0 ? "text-profit" : "text-loss"
                      }`}
                    >
                      ${fmt(pod.total_pnl)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Positions
                    </p>
                    <p className="text-sm font-bold tabular-nums">
                      {pod.open_positions ?? "—"}
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
