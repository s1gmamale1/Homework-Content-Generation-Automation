import { useQuery } from "@tanstack/react-query";

import { BatchFunnel } from "@/components/fleet/batch-funnel";
import { MonitorStats } from "@/components/fleet/monitor-stats";
import { WorkerCards } from "@/components/fleet/worker-cards";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";

export function MonitorPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const workers = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers, refetchInterval: 3500 });

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <MonitorStats
          batches={batches.data}
          workers={
            workers.data
              ? { online: workers.data.online, total: workers.data.total }
              : undefined
          }
        />
        <WorkerCards data={workers.data} />
        <BatchFunnel batches={batches.data} />
      </div>
    </>
  );
}
