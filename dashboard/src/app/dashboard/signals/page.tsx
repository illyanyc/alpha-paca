"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/tables/data-table";

interface Signal {
  id: string;
  pod_name: string;
  symbol: string;
  direction: string;
  strength: number;
  status: string;
  created_at: string;
  [key: string]: unknown;
}

const columns: Column<Signal>[] = [
  {
    key: "created_at",
    header: "Time",
    render: (v) => (
      <span className="text-xs tabular-nums">
        {new Date(v as string).toLocaleString()}
      </span>
    ),
  },
  { key: "pod_name", header: "Pod" },
  { key: "symbol", header: "Symbol", className: "font-medium" },
  {
    key: "direction",
    header: "Direction",
    render: (v) => (
      <Badge variant={v === "long" ? "profit" : v === "short" ? "loss" : "secondary"}>
        {String(v)}
      </Badge>
    ),
  },
  {
    key: "strength",
    header: "Strength",
    className: "text-right tabular-nums",
    render: (v) => {
      const n = Number(v);
      return <span className={n >= 0.7 ? "text-profit" : n >= 0.4 ? "text-warning" : "text-muted-foreground"}>{n.toFixed(3)}</span>;
    },
  },
  {
    key: "status",
    header: "Status",
    render: (v) => (
      <Badge
        variant={
          v === "executed" ? "profit" : v === "rejected" ? "loss" : "secondary"
        }
      >
        {String(v)}
      </Badge>
    ),
  },
];

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAPI<Signal[]>("/signals/?limit=100")
      .then(setSignals)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load signals"),
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
      <h1 className="text-2xl font-bold tracking-tight">Signals</h1>
      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}
      <DataTable columns={columns} data={signals} emptyMessage="No signals recorded" />
    </div>
  );
}
