import { useQuery } from "@tanstack/react-query";

import { BatchFunnel } from "@/components/fleet/batch-funnel";
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
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Monitor</h1>
          <p className="mt-1 text-white/55">Workers and live batch progress.</p>
        </div>
        <WorkerCards data={workers.data} />
        <BatchFunnel batches={batches.data} />
      </div>
    </>
  );
}
