"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface StressTestResult {
  id: string;
  scenario_name: string;
  portfolio_impact: number;
  var_impact: number | null;
  worst_pod: string | null;
  calculated_at: string;
  severity: string;
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
  return `${(n * 100).toFixed(2)}%`;
}

export default function StressTestPage() {
  const [tests, setTests] = useState<StressTestResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAPI<StressTestResult[]>("/risk/stress-tests")
      .then(setTests)
      .catch((err) =>
        setError(
          err instanceof Error ? err.message : "Failed to load stress tests",
        ),
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
      <h1 className="text-2xl font-bold tracking-tight">Stress Tests</h1>

      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}

      {tests.length === 0 ? (
        <p className="py-12 text-center text-muted-foreground">
          No stress test results available
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {tests.map((t) => (
            <Card key={t.id}>
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-semibold">
                    {t.scenario_name}
                  </CardTitle>
                  <Badge
                    variant={
                      t.severity === "critical"
                        ? "loss"
                        : t.severity === "warning"
                          ? "warning"
                          : "secondary"
                    }
                  >
                    {t.severity}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {new Date(t.calculated_at).toLocaleString()}
                </p>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <div className="space-y-3">
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">
                      Portfolio Impact
                    </p>
                    <p
                      className={`text-lg font-bold tabular-nums ${
                        t.portfolio_impact < 0 ? "text-loss" : "text-profit"
                      }`}
                    >
                      ${fmt(t.portfolio_impact)}
                    </p>
                  </div>
                  {t.var_impact != null && (
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground">
                        VaR Impact
                      </p>
                      <p className="text-sm font-bold tabular-nums text-warning">
                        {fmtPct(t.var_impact)}
                      </p>
                    </div>
                  )}
                  {t.worst_pod && (
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground">
                        Worst Pod
                      </p>
                      <p className="text-sm font-medium">{t.worst_pod}</p>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
