import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "@/lib/api";

/** One-line worker-liveness indicator for the launch page. Self-owns the
 *  ["workers"] query (react-query dedupes it against the Monitor page). Links
 *  to /monitor for detail. The 0-online amber state is the anti-footgun:
 *  launching with no worker just queues forever. */
export function OnlineStrip() {
  const workers = useQuery({
    queryKey: ["workers"],
    queryFn: api.listWorkers,
    refetchInterval: 3500,
  });
  const data = workers.data;

  if (!data) {
    return <p className="text-xs text-white/45">checking workers…</p>;
  }

  if (data.online === 0) {
    return (
      <Link
        to="/monitor"
        className="inline-flex items-center gap-1.5 text-xs text-amber-300/90 transition-colors hover:text-amber-200"
      >
        <span className="size-1.5 rounded-full bg-amber-400" />
        no machines online — launches will queue until a worker starts
      </Link>
    );
  }

  return (
    <Link
      to="/monitor"
      className="inline-flex items-center gap-1.5 text-xs text-emerald-400/90 transition-colors hover:text-emerald-300"
    >
      <span className="size-1.5 rounded-full bg-emerald-400 shadow-[0_0_10px_2px_rgba(52,211,153,0.7)]" />
      {data.online} {data.online === 1 ? "machine" : "machines"} online
    </Link>
  );
}
