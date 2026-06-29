import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { BatchFunnel } from "@/components/fleet/batch-funnel";
import { MonitorStats } from "@/components/fleet/monitor-stats";
import { WorkerCards } from "@/components/fleet/worker-cards";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";

export function MonitorPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const workers = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers, refetchInterval: 3500 });

  // The whole Monitor is API-only: scope the list once here so the stat tiles
  // and the batch cards stay consistent (MonitorStats sums rollups across the
  // list it's given — feeding it cli batches would count lessons the cards no
  // longer show). cli stays a valid launch transport elsewhere; this is view-only.
  const apiBatches = useMemo(
    () => (batches.data ?? []).filter((b) => b.transport !== "cli"),
    [batches.data],
  );

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <MonitorStats
          batches={apiBatches}
          workers={
            workers.data
              ? { online: workers.data.online, total: workers.data.total }
              : undefined
          }
        />
        <WorkerCards data={workers.data} />
        <BatchFunnel batches={apiBatches} />
      </div>
    </>
  );
}
