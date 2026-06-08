import { useQuery } from "@tanstack/react-query";
import { FleetLauncher } from "@/components/fleet/launcher";
import { WorkerCards } from "@/components/fleet/worker-cards";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";

export function FleetPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const workers = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers, refetchInterval: 3500 });
  const books = useQuery({ queryKey: ["books"], queryFn: api.listBooks, refetchInterval: 3500 });

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fleet</h1>
          <p className="mt-1 text-white/55">Launch a whole subject; watch the workers chew through it.</p>
        </div>
        <div className="grid gap-5 sm:[grid-template-columns:minmax(320px,360px)_1fr]">
          <FleetLauncher books={books.data} batches={batches.data} />
          <WorkerCards data={workers.data} />
        </div>
        {/* Task 4: <BatchFunnel batches={batches.data} /> */}
      </div>
    </>
  );
}
