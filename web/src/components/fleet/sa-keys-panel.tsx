import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { keyLabel } from "@/lib/sa-key-label";
import { CARD, GHOST_BTN, GLASS_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

export function SaKeysPanel() {
  const qc = useQueryClient();
  const keysQ = useQuery({ queryKey: ["sa-keys"], queryFn: api.listSaKeys });
  const asgQ = useQuery({ queryKey: ["sa-key-assignments"], queryFn: api.listSaKeyAssignments });
  const workersQ = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers });
  const [err, setErr] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadSaKey(f),
    onError: (e: Error) => setErr(e.message),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["sa-keys"] });
      toast.success("Service-account key uploaded");
    },
  });

  const assign = useMutation({
    mutationFn: ({ host, key }: { host: string; key: string }) =>
      api.assignSaKey(host, key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sa-key-assignments"] });
      qc.invalidateQueries({ queryKey: ["sa-keys"] });
      toast.success("Key assigned");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const unassign = useMutation({
    mutationFn: (host: string) => api.unassignSaKey(host),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sa-key-assignments"] });
      qc.invalidateQueries({ queryKey: ["sa-keys"] });
      toast.success("Key unassigned");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const scrub = useMutation({
    mutationFn: (host: string) => api.scrubSaKey(host),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sa-key-assignments"] });
      toast.success("Key scrubbed from host");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const del = useMutation({
    mutationFn: (id: string) => api.deleteSaKey(id),
    onError: (e: Error) => setErr(e.message),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sa-keys"] });
      toast.success("Key deleted");
    },
  });

  const keys = keysQ.data?.keys ?? [];
  const assignments = asgQ.data?.assignments ?? [];
  const hosts = Array.from(
    new Set((workersQ.data?.workers ?? []).map((w) => w.pc_id.split(":")[0])),
  ).sort();
  const asgFor = (h: string) => assignments.find((a) => a.hostname === h) ?? null;

  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold tracking-tight text-white">
          Service-account keys
        </h2>
        <span className="font-mono text-[0.72rem] text-white/45">
          {keys.length} key{keys.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className={cn(CARD, "space-y-5")}>
        {/* Upload */}
        <div className="space-y-2">
          <p className="text-[0.72rem] font-medium text-white/50 uppercase tracking-wider">
            Upload key
          </p>
          <label
            className={cn(
              GLASS_BTN,
              "cursor-pointer text-[0.8rem]",
              upload.isPending && "opacity-50 pointer-events-none",
            )}
          >
            {upload.isPending ? "Uploading…" : "Choose .json file"}
            <input
              type="file"
              accept="application/json"
              className="hidden"
              disabled={upload.isPending}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                // Reset so same file can be re-selected
                e.target.value = "";
              }}
            />
          </label>
          {err && (
            <p role="alert" className="text-[0.75rem] text-red-400/90 mt-1">
              {err}
            </p>
          )}
        </div>

        {/* Key pool */}
        {keys.length > 0 && (
          <div className="space-y-2">
            <p className="text-[0.72rem] font-medium text-white/50 uppercase tracking-wider">
              Key pool
            </p>
            <ul className="space-y-1.5">
              {keys.map((k) => (
                <li
                  key={k.id}
                  className="flex items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2"
                >
                  <span className="flex-1 min-w-0">
                    <code className="font-mono text-[0.75rem] text-white">
                      {keyLabel(k.original_filename, k.project_id)}
                    </code>
                    <span className="ml-2 text-[0.7rem] text-white/45 truncate">
                      {k.project_id} · {k.client_email}
                    </span>
                  </span>
                  <span className="font-mono text-[0.62rem] text-white/35 shrink-0">
                    {k.worker_count} worker{k.worker_count !== 1 ? "s" : ""}
                  </span>
                  <button
                    className={cn(
                      GHOST_BTN,
                      "h-6 px-1.5 text-[0.68rem] text-red-400/70 hover:text-red-300 disabled:opacity-40",
                    )}
                    disabled={k.worker_count > 0 || del.isPending}
                    title={k.worker_count > 0 ? "Unassign all workers first" : "Delete key"}
                    onClick={() => del.mutate(k.id)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Per-host assignment table */}
        {hosts.length > 0 && (
          <div className="space-y-2">
            <p className="text-[0.72rem] font-medium text-white/50 uppercase tracking-wider">
              Host assignments
            </p>
            <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
              <table className="w-full text-[0.75rem]">
                <thead>
                  <tr className="border-b border-white/[0.06] text-left">
                    <th className="px-3 py-2 font-medium text-white/45">Host</th>
                    <th className="px-3 py-2 font-medium text-white/45">Assigned key</th>
                    <th className="px-3 py-2 font-medium text-white/45">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {hosts.map((h) => {
                    const a = asgFor(h);
                    const isPendingAssign =
                      assign.isPending &&
                      (assign.variables as { host: string } | undefined)?.host === h;
                    const isPendingUnassign =
                      unassign.isPending && unassign.variables === h;
                    const isPendingScrub =
                      scrub.isPending && scrub.variables === h;

                    return (
                      <tr
                        key={h}
                        className="border-b border-white/[0.04] last:border-0"
                      >
                        <td className="px-3 py-2">
                          <span className="font-mono text-white">{h}</span>
                        </td>
                        <td className="px-3 py-2 text-white/60">
                          {a?.scrub
                            ? "(scrubbed)"
                            : a?.key_id
                              ? (() => {
                                  const k = keys.find((kk) => kk.id === a.key_id);
                                  return k
                                    ? keyLabel(k.original_filename, k.project_id)
                                    : (a.project_id ?? "—");
                                })()
                              : "—"}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <select
                              className="rounded-lg border border-white/[0.1] bg-white/[0.05] px-2 py-1 text-[0.72rem] text-white/80 focus:outline-none focus:ring-1 focus:ring-white/20"
                              defaultValue={a?.key_id ?? ""}
                              disabled={isPendingAssign}
                              onChange={(e) => {
                                if (e.target.value) {
                                  assign.mutate({ host: h, key: e.target.value });
                                }
                              }}
                            >
                              {/* Native <select> popup is OS-rendered on a white
                                  background — options need an explicit dark bg +
                                  light text or they're white-on-white/invisible. */}
                              <option value="" className="bg-neutral-900 text-white">
                                Assign key…
                              </option>
                              {keys.map((k) => (
                                <option
                                  key={k.id}
                                  value={k.id}
                                  className="bg-neutral-900 text-white"
                                >
                                  {keyLabel(k.original_filename, k.project_id)}
                                </option>
                              ))}
                            </select>
                            <button
                              className={cn(
                                GHOST_BTN,
                                "h-6 px-1.5 text-[0.68rem] disabled:opacity-40",
                              )}
                              disabled={isPendingUnassign || !a?.key_id}
                              onClick={() => unassign.mutate(h)}
                            >
                              {isPendingUnassign ? "…" : "Unassign"}
                            </button>
                            <button
                              className={cn(
                                GHOST_BTN,
                                "h-6 px-1.5 text-[0.68rem] text-amber-300/70 hover:text-amber-200 disabled:opacity-40",
                              )}
                              disabled={isPendingScrub}
                              onClick={() => scrub.mutate(h)}
                            >
                              {isPendingScrub ? "…" : "Scrub"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {hosts.length === 0 && keys.length === 0 && (
          <p className="text-sm text-white/40">
            No workers or keys yet. Upload a service-account key to get started.
          </p>
        )}
      </div>
    </div>
  );
}
