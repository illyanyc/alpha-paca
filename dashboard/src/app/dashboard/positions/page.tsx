"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/tables/data-table";

interface Position {
  id: string;
  symbol: string;
  pod_name: string;
  side: string;
  quantity: number;
  entry_price: number;
  current_price: number | null;
  unrealized_pnl: number | null;
  status: string;
  entry_time: string;
  [key: string]: unknown;
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

const columns: Column<Position>[] = [
  { key: "symbol", header: "Symbol", className: "font-medium" },
  { key: "pod_name", header: "Pod" },
  {
    key: "side",
    header: "Side",
    render: (v) => (
      <Badge variant={v === "long" ? "profit" : "loss"}>{String(v)}</Badge>
    ),
  },
  {
    key: "quantity",
    header: "Qty",
    className: "text-right tabular-nums",
    render: (v) => fmt(v as number, 0),
  },
  {
    key: "entry_price",
    header: "Entry",
    className: "text-right tabular-nums",
    render: (v) => `$${fmt(v as number)}`,
  },
  {
    key: "current_price",
    header: "Current",
    className: "text-right tabular-nums",
    render: (v) => (v != null ? `$${fmt(v as number)}` : "—"),
  },
  {
    key: "unrealized_pnl",
    header: "Unreal. P&L",
    className: "text-right tabular-nums",
    render: (v) => {
      const n = v as number | null;
      if (n == null) return "—";
      return (
        <span className={n >= 0 ? "text-profit" : "text-loss"}>
          ${fmt(n)}
        </span>
      );
    },
  },
  {
    key: "status",
    header: "Status",
    render: (v) => (
      <Badge
        variant={
          v === "open" ? "profit" : v === "closed" ? "secondary" : "warning"
        }
      >
        {String(v)}
      </Badge>
    ),
  },
  {
    key: "entry_time",
    header: "Opened",
    render: (v) => (
      <span className="text-xs tabular-nums">
        {new Date(v as string).toLocaleString()}
      </span>
    ),
  },
];

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAPI<Position[]>("/positions/")
      .then(setPositions)
      .catch((err) =>
        setError(
          err instanceof Error ? err.message : "Failed to load positions",
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
      <h1 className="text-2xl font-bold tracking-tight">Positions</h1>
      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}
      <DataTable
        columns={columns}
        data={positions}
        emptyMessage="No positions"
      />
    </div>
  );
}
