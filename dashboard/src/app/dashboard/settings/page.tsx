"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { fetchAPI, postAPI, putAPI } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

/* ── Types ────────────────────────────────────────────────────────────── */

interface TunableSetting {
  key: string;
  value: unknown;
  default_value: unknown;
  type: string;
  category: string;
  label: string;
  description: string;
  min: number | null;
  max: number | null;
  step: number | null;
}

interface OptimizationSuggestion {
  key: string;
  current: unknown;
  suggested: unknown;
  reason: string;
}

/* ── Slider with synced numeric input ────────────────────────────────── */

function SliderWithInput({
  value,
  min,
  max,
  step,
  onChange,
  disabled,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  const [inputText, setInputText] = useState(String(value));

  useEffect(() => {
    setInputText(String(value));
  }, [value]);

  const commitInput = () => {
    const parsed = parseFloat(inputText);
    if (!isNaN(parsed)) {
      const clamped = Math.min(max, Math.max(min, parsed));
      onChange(clamped);
      setInputText(String(clamped));
    } else {
      setInputText(String(value));
    }
  };

  return (
    <div className="flex items-center gap-3">
      <SliderPrimitive.Root
        className="relative flex h-5 w-full touch-none select-none items-center"
        value={[value]}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onValueChange={([v]) => onChange(v)}
      >
        <SliderPrimitive.Track className="relative h-1.5 w-full grow rounded-full bg-secondary">
          <SliderPrimitive.Range className="absolute h-full rounded-full bg-primary" />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb className="block h-4 w-4 rounded-full border-2 border-primary bg-background shadow transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50" />
      </SliderPrimitive.Root>
      <input
        type="text"
        inputMode="decimal"
        value={inputText}
        disabled={disabled}
        onChange={(e) => setInputText(e.target.value)}
        onBlur={commitInput}
        onKeyDown={(e) => e.key === "Enter" && commitInput()}
        className="w-24 rounded-md border border-border bg-background px-2 py-1 text-right text-sm tabular-nums placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      />
    </div>
  );
}

/* ── Setting field renderer ──────────────────────────────────────────── */

function SettingField({
  setting,
  localValue,
  onUpdate,
  onReset,
}: {
  setting: TunableSetting;
  localValue: unknown;
  onUpdate: (value: unknown) => void;
  onReset: () => void;
}) {
  const isModified =
    JSON.stringify(localValue) !== JSON.stringify(setting.default_value);

  if (setting.type === "bool" || setting.type === "boolean") {
    const checked = Boolean(localValue);
    return (
      <div className="flex items-center justify-between rounded-lg border border-border/50 p-3">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">{setting.label}</p>
            {isModified && <Badge variant="warning">modified</Badge>}
          </div>
          {setting.description && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {setting.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onUpdate(!checked)}
            className={cn(
              "relative h-6 w-10 rounded-full transition-colors",
              checked ? "bg-primary" : "bg-secondary",
            )}
          >
            <span
              className={cn(
                "absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
                checked && "translate-x-4",
              )}
            />
          </button>
          {isModified && (
            <button
              onClick={onReset}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Reset
            </button>
          )}
        </div>
      </div>
    );
  }

  const numValue = Number(localValue ?? setting.default_value ?? 0);
  const min = setting.min ?? 0;
  const max = setting.max ?? 100;
  const step = setting.step ?? (max - min > 10 ? 1 : 0.01);

  return (
    <div className="rounded-lg border border-border/50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">{setting.label}</p>
          {isModified && <Badge variant="warning">modified</Badge>}
        </div>
        {isModified && (
          <button
            onClick={onReset}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Reset
          </button>
        )}
      </div>
      {setting.description && (
        <p className="mb-2 text-xs text-muted-foreground">
          {setting.description}
        </p>
      )}
      <SliderWithInput
        value={numValue}
        min={min}
        max={max}
        step={step}
        onChange={(v) => onUpdate(v)}
      />
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground/70">
        <span>{min}</span>
        <span>default: {String(setting.default_value)}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}

/* ── Settings Page ───────────────────────────────────────────────────── */

export default function SettingsPage() {
  const [settings, setSettings] = useState<TunableSetting[]>([]);
  const [localValues, setLocalValues] = useState<Record<string, unknown>>({});
  const [categories, setCategories] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [suggestions, setSuggestions] = useState<OptimizationSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [tunables, cats] = await Promise.all([
          fetchAPI<TunableSetting[]>("/settings/tunable"),
          fetchAPI<string[]>("/settings/tunable/categories"),
        ]);
        setSettings(tunables);
        setCategories(cats);
        if (cats.length > 0 && !activeTab) setActiveTab(cats[0]);

        const vals: Record<string, unknown> = {};
        for (const s of tunables) vals[s.key] = s.value;
        setLocalValues(vals);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load settings");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filteredSettings = useMemo(
    () => settings.filter((s) => s.category === activeTab),
    [settings, activeTab],
  );

  const dirtyKeys = useMemo(() => {
    const dirty: Record<string, unknown> = {};
    for (const s of filteredSettings) {
      if (JSON.stringify(localValues[s.key]) !== JSON.stringify(s.value)) {
        dirty[s.key] = localValues[s.key];
      }
    }
    return dirty;
  }, [filteredSettings, localValues]);

  const hasDirty = Object.keys(dirtyKeys).length > 0;

  const updateLocal = useCallback((key: string, value: unknown) => {
    setLocalValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  const resetLocal = useCallback(
    (key: string) => {
      const s = settings.find((x) => x.key === key);
      if (s) setLocalValues((prev) => ({ ...prev, [key]: s.default_value }));
    },
    [settings],
  );

  const applyChanges = async () => {
    if (!hasDirty) return;
    setSaving(true);
    setError(null);
    try {
      await putAPI("/settings/tunable/batch", { updates: dirtyKeys });
      const tunables = await fetchAPI<TunableSetting[]>("/settings/tunable");
      setSettings(tunables);
      const vals: Record<string, unknown> = {};
      for (const s of tunables) vals[s.key] = s.value;
      setLocalValues(vals);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const runOptimize = async () => {
    setOptimizing(true);
    setError(null);
    try {
      const result = await postAPI<{ suggestions?: OptimizationSuggestion[] }>(
        "/settings/optimize",
        {},
      );
      setSuggestions(result.suggestions ?? []);
      setShowSuggestions(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Optimization failed");
    } finally {
      setOptimizing(false);
    }
  };

  const applySuggestion = (key: string, value: unknown) => {
    setLocalValues((prev) => ({ ...prev, [key]: value }));
  };

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <button
          onClick={runOptimize}
          disabled={optimizing}
          className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-violet-600 to-indigo-600 px-4 py-2 text-sm font-medium text-white shadow transition hover:from-violet-500 hover:to-indigo-500 disabled:opacity-50"
        >
          {optimizing ? (
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
          ) : (
            <svg
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 10V3L4 14h7v7l9-11h-7z"
              />
            </svg>
          )}
          AI Optimize
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
          {error}
        </div>
      )}

      {/* AI Optimization Suggestions */}
      {showSuggestions && suggestions.length > 0 && (
        <Card className="border-violet-500/30">
          <CardHeader className="p-4 pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold text-violet-400">
                AI Optimization Suggestions
              </CardTitle>
              <button
                onClick={() => setShowSuggestions(false)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Dismiss
              </button>
            </div>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="space-y-2">
              {suggestions.map((s) => (
                <div
                  key={s.key}
                  className="flex items-start gap-3 rounded-md border border-violet-500/20 bg-violet-500/5 p-3"
                >
                  <div className="flex-1">
                    <p className="text-sm font-medium">{s.key}</p>
                    <p className="text-xs text-muted-foreground">{s.reason}</p>
                    <p className="mt-1 text-xs">
                      <span className="text-muted-foreground">
                        {String(s.current)}
                      </span>
                      <span className="mx-2 text-muted-foreground/50">→</span>
                      <span className="font-medium text-violet-400">
                        {String(s.suggested)}
                      </span>
                    </p>
                  </div>
                  <button
                    onClick={() => applySuggestion(s.key, s.suggested)}
                    className="shrink-0 rounded-md bg-violet-600/80 px-3 py-1 text-xs font-medium text-white transition hover:bg-violet-500"
                  >
                    Apply
                  </button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Category Tabs */}
      <div className="flex flex-wrap gap-1.5">
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveTab(cat)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors",
              activeTab === cat
                ? "bg-primary text-primary-foreground"
                : "bg-secondary/50 text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {cat.replace(/_/g, " ")}
          </button>
        ))}
      </div>

      {/* Setting Fields */}
      <div className="space-y-3">
        {filteredSettings.map((s) => (
          <SettingField
            key={s.key}
            setting={s}
            localValue={localValues[s.key]}
            onUpdate={(v) => updateLocal(s.key, v)}
            onReset={() => resetLocal(s.key)}
          />
        ))}
        {filteredSettings.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No settings in this category
          </p>
        )}
      </div>

      {/* Apply Changes */}
      {hasDirty && (
        <div className="sticky bottom-4 flex justify-end">
          <button
            onClick={applyChanges}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground shadow-lg transition hover:bg-primary/90 disabled:opacity-50"
          >
            {saving && (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            )}
            Apply Changes ({Object.keys(dirtyKeys).length})
          </button>
        </div>
      )}
    </div>
  );
}
