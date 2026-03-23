"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface RegimeData {
  dominant: string;
  probabilities: Record<string, number>;
  confidence: number;
}

interface CircuitBreakerStatus {
  system_level: string;
  pod_levels: Record<string, string>;
  consecutive_losses: Record<string, number>;
  daily_pnl_pct: Record<string, number>;
}

interface SystemHealth {
  api: string;
  orchestrator: string;
  scheduler: string;
  circuit_breaker: string;
  drift: string;
  regime: string;
}

const REGIME_COLORS: Record<string, string> = {
  bull_trend: "bg-green-600",
  bear_trend: "bg-red-600",
  sideways: "bg-yellow-600",
  crisis: "bg-purple-600",
  unknown: "bg-gray-600",
};

const CB_COLORS: Record<string, string> = {
  closed: "bg-green-600",
  transient: "bg-yellow-500",
  degraded: "bg-orange-500",
  strategy_halt: "bg-red-500",
  system_halt: "bg-red-700",
  emergency: "bg-red-900",
};

export default function RegimePage() {
  const [regime, setRegime] = useState<RegimeData | null>(null);
  const [cb, setCb] = useState<CircuitBreakerStatus | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [regimeRes, cbRes, healthRes] = await Promise.all([
          fetch(`${API_BASE}/api/regime/current`),
          fetch(`${API_BASE}/api/health/circuit-breaker`),
          fetch(`${API_BASE}/api/health/system`),
        ]);
        const regimeJson = await regimeRes.json();
        const cbJson = await cbRes.json();
        const healthJson = await healthRes.json();
        setRegime(regimeJson);
        setCb(
          cbJson && typeof cbJson === "object" && "system_level" in cbJson
            ? (cbJson as CircuitBreakerStatus)
            : null
        );
        setHealth(healthJson);
      } catch {
        /* retry on next interval */
      }
    };
    fetchAll();
    const interval = setInterval(fetchAll, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-bold">Regime & System Health</h1>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {/* Current Regime */}
        <Card>
          <CardHeader>
            <CardTitle>Market Regime</CardTitle>
          </CardHeader>
          <CardContent>
            {regime ? (
              <div className="space-y-4">
                <Badge className={`${REGIME_COLORS[regime.dominant] || "bg-gray-600"} text-white text-lg px-4 py-2`}>
                  {regime.dominant.replace("_", " ").toUpperCase()}
                </Badge>
                <p className="text-sm text-muted-foreground">
                  Confidence: {(regime.confidence * 100).toFixed(1)}%
                </p>
                <div className="space-y-2">
                  {Object.entries(regime.probabilities).map(([state, prob]) => (
                    <div key={state} className="flex items-center justify-between">
                      <span className="text-sm capitalize">{state.replace("_", " ")}</span>
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-24 rounded bg-muted overflow-hidden">
                          <div
                            className={`h-full rounded ${REGIME_COLORS[state] || "bg-gray-400"}`}
                            style={{ width: `${prob * 100}%` }}
                          />
                        </div>
                        <span className="text-sm font-mono w-12 text-right">
                          {(prob * 100).toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-muted-foreground">Loading...</p>
            )}
          </CardContent>
        </Card>

        {/* Circuit Breaker */}
        <Card>
          <CardHeader>
            <CardTitle>Circuit Breaker</CardTitle>
          </CardHeader>
          <CardContent>
            {cb ? (
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">System:</span>
                  <Badge className={`${CB_COLORS[cb.system_level] || "bg-gray-600"} text-white`}>
                    {cb.system_level.toUpperCase()}
                  </Badge>
                </div>
                <div className="space-y-2">
                  {Object.entries(cb.pod_levels).map(([pod, level]) => (
                    <div key={pod} className="flex items-center justify-between">
                      <span className="text-sm">{pod}</span>
                      <Badge variant="outline" className={level !== "closed" ? "border-red-500 text-red-500" : ""}>
                        {level}
                      </Badge>
                    </div>
                  ))}
                </div>
                {Object.keys(cb.daily_pnl_pct).length > 0 && (
                  <div className="space-y-1 pt-2 border-t">
                    <p className="text-xs font-medium text-muted-foreground">Daily P&L</p>
                    {Object.entries(cb.daily_pnl_pct).map(([pod, pnl]) => (
                      <div key={pod} className="flex justify-between text-sm">
                        <span>{pod}</span>
                        <span className={pnl >= 0 ? "text-green-500" : "text-red-500"}>
                          {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}%
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-muted-foreground">Loading...</p>
            )}
          </CardContent>
        </Card>

        {/* System Health */}
        <Card>
          <CardHeader>
            <CardTitle>System Health</CardTitle>
          </CardHeader>
          <CardContent>
            {health ? (
              <div className="space-y-2">
                {Object.entries(health).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between">
                    <span className="text-sm capitalize">{key.replace("_", " ")}</span>
                    <Badge
                      variant={value === "ok" ? "default" : "destructive"}
                      className={value === "ok" ? "bg-green-600" : ""}
                    >
                      {value}
                    </Badge>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground">Loading...</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
