"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface FactorExposure {
  id: string;
  snapshot_time: string;
  exposures: Record<string, number>;
  [key: string]: unknown;
}

function fmt(n: number, d = 4): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

function barWidth(v: number, maxAbs: number): string {
  if (maxAbs === 0) return "0%";
  return `${(Math.abs(v) / maxAbs) * 100}%`;
}

export default function FactorsPage() {
  const [data, setData] = useState<FactorExposure | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAPI<FactorExposure>("/risk/factors")
      .then(setData)
      .catch((err) =>
        setError(
          err instanceof Error ? err.message : "Failed to load factor exposures",
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

  const exposures = data?.exposures ?? {};
  const entries = Object.entries(exposures).sort(
    ([, a], [, b]) => Math.abs(b) - Math.abs(a),
  );
  const maxAbs = entries.reduce((m, [, v]) => Math.max(m, Math.abs(v)), 0);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Factor Exposures</h1>

      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}

      {data && (
        <p className="text-xs text-muted-foreground">
          Snapshot: {new Date(data.snapshot_time).toLocaleString()}
        </p>
      )}

      {entries.length === 0 ? (
        <p className="py-12 text-center text-muted-foreground">
          No factor exposure data available
        </p>
      ) : (
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-semibold">
              Current Exposures
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="space-y-3">
              {entries.map(([factor, value]) => (
                <div key={factor} className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium capitalize">
                      {factor.replace(/_/g, " ")}
                    </span>
                    <span
                      className={`text-sm font-bold tabular-nums ${
                        value >= 0 ? "text-profit" : "text-loss"
                      }`}
                    >
                      {fmt(value)}
                    </span>
                  </div>
                  <div className="flex h-2 items-center">
                    <div className="relative h-full w-full rounded-full bg-secondary/50">
                      <div
                        className={`absolute h-full rounded-full ${
                          value >= 0 ? "bg-profit/40 left-1/2" : "bg-loss/40 right-1/2"
                        }`}
                        style={{ width: `calc(${barWidth(value, maxAbs)} / 2)` }}
                      />
                      <div className="absolute left-1/2 top-0 h-full w-px bg-border" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
