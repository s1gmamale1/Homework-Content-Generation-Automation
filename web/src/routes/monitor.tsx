import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { BatchFunnel } from "@/components/fleet/batch-funnel";
import { MonitorStats } from "@/components/fleet/monitor-stats";
import { WorkerCards } from "@/components/fleet/worker-cards";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  type Lang,
  type StatusFilter,
  STATUS_FILTERS,
  summarizeByLanguage,
} from "@/lib/monitor-filters";

const LANG_LABEL: Record<Lang, string> = { uz: "Uzbek", en: "English", ru: "Russian" };
const STATUS_LABEL: Record<StatusFilter, string> = {
  attention: "Needs attention",
  all: "All",
  running: "Running",
  failed: "Failed",
  paused: "Paused",
  complete: "Complete",
};

export function MonitorPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const workers = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers, refetchInterval: 3500 });

  // The whole Monitor is API-only: scope cli batches out once here (#63) so the
  // stat tiles and the cards stay consistent (MonitorStats sums rollups across
  // the list it's given). cli stays a valid launch transport elsewhere; view-only.
  const apiBatches = useMemo(
    () => (batches.data ?? []).filter((b) => b.transport !== "cli"),
    [batches.data],
  );

  // Language dimension (Phase 2) composes on top of the API-only scope: tabs +
  // counts derive from apiBatches, and the active language further scopes it.
  const langs = summarizeByLanguage(apiBatches);

  const [activeLang, setActiveLang] = useState<Lang>("uz");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("attention");

  // Keep activeLang valid as data loads: if the current lang has no batches,
  // jump to the first available one (or fall back to "uz" when langs is empty).
  useEffect(() => {
    if (langs.length === 0) return;
    const found = langs.find((l) => l.lang === activeLang);
    if (!found) setActiveLang(langs[0].lang);
  }, [langs, activeLang]);

  const scoped = apiBatches.filter((b) => b.output_language === activeLang);

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        {/* Language tab bar — only when there are batches */}
        {langs.length > 0 && (
          <div className="flex gap-1 rounded-2xl border border-white/[0.08] bg-white/[0.04] p-1 backdrop-blur-xl">
            {langs.map((s) => (
              <button
                key={s.lang}
                type="button"
                onClick={() => setActiveLang(s.lang)}
                className={cn(
                  "flex flex-col items-start rounded-xl px-4 py-2 text-left transition-colors",
                  activeLang === s.lang
                    ? "bg-white/[0.1] text-white"
                    : "text-white/55 hover:text-white/80",
                )}
              >
                <span className="text-sm font-semibold">{LANG_LABEL[s.lang]}</span>
                <span className="font-mono text-[0.65rem] text-white/40">
                  {s.lessons} lessons · {s.failed} failed · {s.running} running
                </span>
              </button>
            ))}
          </div>
        )}

        {/* Status filter bar */}
        <div className="flex flex-wrap gap-1">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setStatusFilter(f)}
              className={cn(
                "rounded-xl px-3 py-1.5 text-xs font-medium transition-colors",
                statusFilter === f
                  ? "bg-white/[0.12] text-white"
                  : "text-white/45 hover:text-white/70",
              )}
            >
              {STATUS_LABEL[f]}
            </button>
          ))}
        </div>

        <MonitorStats
          batches={scoped}
          workers={
            workers.data
              ? { online: workers.data.online, total: workers.data.total }
              : undefined
          }
          onFilter={setStatusFilter}
        />
        <WorkerCards data={workers.data} />
        <BatchFunnel batches={scoped} statusFilter={statusFilter} />
      </div>
    </>
  );
}
