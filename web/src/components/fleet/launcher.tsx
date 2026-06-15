import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Loader2, Rocket } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { subjectLabel } from "@/lib/subjects";
import type { BatchSummary, Book, RoleTransport, Transport } from "@/lib/types";
import { CARD, GHOST_BTN, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";

const LBL = "text-xs font-medium uppercase tracking-[0.12em] text-white/45";

/** All transports a book could be launched on. cli is always available; api
 *  only for providers the backend `api_supported` map marks true. */
const ALL_TRANSPORTS: Transport[] = ["cli", "api"];

/** Per-role billing options for the Extract/Judge selects. "Auto" (inherit)
 *  follows the batch's transport. */
const ROLE_TRANSPORT_OPTIONS: { value: RoleTransport; label: string }[] = [
  { value: "inherit", label: "Auto" },
  { value: "cli", label: "CLI" },
  { value: "api", label: "API" },
];

export function FleetLauncher({
  books,
  batches,
}: {
  books?: Book[];
  batches?: BatchSummary[];
}) {
  const qc = useQueryClient();

  const [gradePageId, setGradePageId] = useState("");
  const [gradeDigits, setGradeDigits] = useState("");
  const [subjectPageId, setSubjectPageId] = useState("");

  const gradesQ = useQuery({
    queryKey: ["notion-grades"],
    queryFn: api.listNotionGrades,
  });
  const subjectsQ = useQuery({
    queryKey: ["notion-subjects", gradePageId],
    queryFn: () => api.listNotionSubjects(gradePageId),
    enabled: !!gradePageId,
  });

  const pickedSubject = subjectsQ.data?.find((s) => s.page_id === subjectPageId);
  const subjectUsable = !!pickedSubject?.app_subject && !!pickedSubject?.has_textbook;

  const prepare = useMutation({
    mutationFn: (v: { subjectPageId: string; grade: string }) =>
      api.fetchBookFromNotion(v.subjectPageId, v.grade),
    onSuccess: () => {
      toast.success("Preparing — extracting lessons…");
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Prepare failed"),
  });

  // ---- Tray (server-derived) ----
  // The batch key is (book_id, transport): a book can carry a cli batch AND an
  // api batch independently. Track which transports each book already has, so a
  // cli-batched book is still launchable on api (and vice-versa). A book leaves
  // the Ready tray only once it has a batch for EVERY transport.
  const batchedTransports = new Map<string, Set<Transport>>();
  for (const b of batches ?? []) {
    const set = batchedTransports.get(b.book_id) ?? new Set<Transport>();
    set.add(b.transport);
    batchedTransports.set(b.book_id, set);
  }
  const fullyBatched = (bookId: string) => {
    const set = batchedTransports.get(bookId);
    return !!set && ALL_TRANSPORTS.every((t) => set.has(t));
  };
  const all = books ?? [];
  const preparing = all.filter(
    (b) => b.status === "toc_extracting" || b.status === "uploading",
  );
  const failed = all.filter((b) => b.status === "failed");
  const ready = all.filter(
    (b) => b.status === "toc_ready" && !fullyBatched(b.id),
  );
  const trayEmpty =
    preparing.length === 0 && ready.length === 0 && failed.length === 0;

  return (
    <div className={cn(CARD, "space-y-6")}>
      {/* Part A — Prepare */}
      <div className="space-y-4">
        <div>
          <h2 className="text-sm font-semibold tracking-tight text-white">Prepare a subject</h2>
          <p className="mt-1 text-xs text-white/45">
            Pull a textbook from Notion, then launch it across the fleet.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className={LBL}>Grade</span>
          <Select
            value={gradePageId}
            onValueChange={(pageId) => {
              const g = gradesQ.data?.find((x) => x.page_id === pageId);
              setGradePageId(pageId);
              setGradeDigits(g ? g.title.replace(/\D/g, "") : "");
              setSubjectPageId("");
            }}
            disabled={gradesQ.isLoading}
          >
            <SelectTrigger className={SELECT_TRIGGER}>
              <SelectValue placeholder={gradesQ.isLoading ? "Loading grades…" : "Choose a grade"} />
            </SelectTrigger>
            <SelectContent>
              {(gradesQ.data ?? []).map((g) => (
                <SelectItem key={g.page_id} value={g.page_id}>
                  {g.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {gradePageId && (
          <div className="flex flex-col gap-1.5">
            <span className={LBL}>Subject</span>
            <Select
              value={subjectPageId}
              onValueChange={setSubjectPageId}
              disabled={subjectsQ.isLoading}
            >
              <SelectTrigger className={SELECT_TRIGGER}>
                <SelectValue
                  placeholder={subjectsQ.isLoading ? "Loading subjects…" : "Choose a subject"}
                />
              </SelectTrigger>
              <SelectContent>
                {(subjectsQ.data ?? []).map((s) => (
                  <SelectItem
                    key={s.page_id}
                    value={s.page_id}
                    disabled={!s.has_textbook || !s.app_subject}
                  >
                    {s.notion_title}
                    {!s.has_textbook
                      ? " · no textbook"
                      : !s.app_subject
                        ? " · unsupported"
                        : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <button
          type="button"
          className={PRIMARY_BTN}
          disabled={!subjectUsable || prepare.isPending}
          onClick={() =>
            prepare.mutate({ subjectPageId, grade: gradeDigits })
          }
        >
          {prepare.isPending ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Preparing…
            </>
          ) : (
            <>
              <Rocket className="size-4" />
              Prepare
            </>
          )}
        </button>
      </div>

      {/* Part B — Tray */}
      <div className="space-y-3 border-t border-white/[0.08] pt-5">
        <h3 className="text-sm font-semibold tracking-tight text-white">Tray</h3>

        {trayEmpty ? (
          <p className="text-sm text-white/45">Prepare a subject above to get started.</p>
        ) : (
          <div className="space-y-4">
            {preparing.length > 0 && (
              <div className="space-y-2">
                <span className={LBL}>Preparing</span>
                {preparing.map((b) => (
                  <div
                    key={b.id}
                    className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5"
                  >
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-white">
                        {subjectLabel(b.subject)}
                      </div>
                      <div className="truncate text-xs text-white/45">
                        {b.original_filename}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs text-white/45">
                      <Loader2 className="size-4 animate-spin" />
                      <span>extracting lessons… ~1–3 min</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {ready.length > 0 && (
              <div className="space-y-2">
                <span className={LBL}>Ready</span>
                {ready.map((b) => (
                  <ReadyRow
                    key={b.id}
                    book={b}
                    batchedTransports={batchedTransports.get(b.id) ?? new Set()}
                  />
                ))}
              </div>
            )}

            {failed.length > 0 && (
              <div className="space-y-2">
                <span className={LBL}>Failed</span>
                {failed.map((b) => (
                  <div
                    key={b.id}
                    className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5"
                  >
                    <div className="text-sm font-medium text-white">
                      {subjectLabel(b.subject)}
                    </div>
                    <div className="mt-0.5 text-xs text-red-300/80">
                      {b.error_message ?? "Extraction failed."}
                    </div>
                    {/* retry handled via re-prepare in Task 5 */}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ReadyRow({
  book,
  batchedTransports,
}: {
  book: Book;
  batchedTransports: Set<Transport>;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [provider, setProvider] = useState("claude");
  const [transport, setTransport] = useState<Transport>("cli");
  const [extractTransport, setExtractTransport] = useState<RoleTransport>("inherit");
  const [judgeTransport, setJudgeTransport] = useState<RoleTransport>("inherit");
  const [model, setModel] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const modelsQ = useQuery({
    queryKey: ["agent-models"],
    queryFn: api.getAgentModels,
  });
  const detail = useQuery({
    queryKey: ["book", book.id],
    queryFn: () => api.getBook(book.id),
  });
  const toc = detail.data?.toc ?? [];
  const lessons = detail.data?.toc?.length;
  const doneCount = toc.filter((t) => t.latest_job_status === "done").length;
  const complete = lessons != null && lessons > 0 && doneCount === lessons;
  const subset = choosing && selected.size > 0;

  // Does the picked provider support the pay-per-token API transport? Only
  // claude/gemini do; the toggle is hidden for the rest and transport pins cli.
  const apiSupported = modelsQ.data?.api_supported?.[provider] ?? false;

  // Reset transport to cli whenever the provider can't do api (keeps an
  // unsupported provider from carrying a stale api selection). model=null means
  // "provider default" — fine for cli, but api forces an explicit model below.
  useEffect(() => {
    if (!apiSupported && transport === "api") setTransport("cli");
  }, [apiSupported, transport]);

  // On api, force a concrete model: "provider default" isn't allowed (billing
  // needs an explicit model). Seed/clear the model as transport/provider change.
  const modelOptions = modelsQ.data?.providers?.[provider] ?? [];
  useEffect(() => {
    if (transport === "api") {
      // Seed the first concrete model if none chosen / stale for this provider.
      if (!model || !modelOptions.includes(model)) {
        setModel(modelOptions[0] ?? null);
      }
    } else {
      // cli uses provider default — don't pin a model.
      setModel(null);
    }
    // modelOptions identity changes only when the manifest/provider changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transport, provider, modelsQ.data]);

  const alreadyBatched = batchedTransports.has(transport);
  // On api we must have an explicit model selected.
  const missingApiModel = transport === "api" && !model;

  const launch = useMutation({
    mutationFn: () =>
      api.launchBatch({
        book_id: book.id,
        provider,
        transport,
        extract_transport: extractTransport,
        judge_transport: judgeTransport,
        ...(transport === "api" ? { model } : {}),
        ...(subset ? { toc_entry_ids: [...selected] } : {}),
      }),
    onSuccess: (r) => {
      toast.success(`Launched ${r.jobs_created} lessons`, {
        action: { label: "View in Monitor", onClick: () => navigate("/monitor") },
      });
      setChoosing(false);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Launch failed"),
  });

  const providers = Object.keys(modelsQ.data?.providers ?? {});

  return (
    <div className="w-full rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-medium text-white">
            {subjectLabel(book.subject)}
            {book.grade && (
              <span className="rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-normal text-white/55">
                Grade {book.grade}
              </span>
            )}
          </div>
          <div className="text-xs text-white/45">
            {lessons ?? "…"} lessons
            {doneCount > 0 && (
              <span className={complete ? "text-emerald-400/80" : undefined}>
                {" · "}
                {complete ? "complete" : `${doneCount}/${lessons} done`}
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            className={cn(GHOST_BTN, "h-9 px-2.5 text-xs")}
            onClick={() => {
              setChoosing((c) => !c);
              setSelected(new Set());
            }}
          >
            <ListChecks className="size-3.5" />
            {choosing ? `Choosing (${selected.size})` : "Choose lessons"}
          </button>
          <Select value={provider} onValueChange={setProvider}>
            <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[8.5rem]")}>
              <SelectValue placeholder="claude" />
            </SelectTrigger>
            <SelectContent>
              {(providers.length > 0 ? providers : ["claude"]).map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* CLI | API toggle — only for providers the backend bills via API. */}
          {apiSupported && (
            <TransportToggle value={transport} onChange={setTransport} />
          )}
          {/* Per-role billing — always visible (a cli batch can still pin its
              extract/judge calls to api, and vice versa). Auto = inherit. */}
          <RoleTransportSelect
            label="Extract"
            value={extractTransport}
            onChange={setExtractTransport}
          />
          <RoleTransportSelect
            label="Judge"
            value={judgeTransport}
            onChange={setJudgeTransport}
          />
          {/* API forces an explicit model (no "provider default"). */}
          {transport === "api" && (
            <Select value={model ?? ""} onValueChange={(v) => setModel(v)}>
              <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[11rem]")}>
                <SelectValue placeholder="Pick a model" />
              </SelectTrigger>
              <SelectContent>
                {modelOptions.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <button
            type="button"
            className={PRIMARY_BTN}
            disabled={
              launch.isPending ||
              (choosing && selected.size === 0) ||
              alreadyBatched ||
              missingApiModel
            }
            title={
              alreadyBatched
                ? `Already launched on ${transport.toUpperCase()}`
                : missingApiModel
                  ? "Pick a model to launch on API"
                  : undefined
            }
            onClick={() => launch.mutate()}
          >
            {launch.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Rocket className="size-4" />
            )}
            {alreadyBatched
              ? `${transport.toUpperCase()} launched`
              : subset
                ? `Launch ${selected.size}`
                : "Launch"}
          </button>
        </div>
      </div>

      {choosing && (
        <div className="mt-2 w-full space-y-1 rounded-xl border border-white/[0.08] bg-black/20 p-2">
          {toc.length === 0 ? (
            <div className="px-1 py-1 text-xs text-white/45">No lessons found.</div>
          ) : (
            toc.map((t) => {
              const on = selected.has(t.id);
              return (
                <label
                  key={t.id}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-1 text-xs text-white/80 hover:bg-white/[0.04]"
                >
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() =>
                      setSelected((prev) => {
                        const next = new Set(prev);
                        if (next.has(t.id)) next.delete(t.id);
                        else next.add(t.id);
                        return next;
                      })
                    }
                    className="size-3.5 shrink-0 accent-[#7c5cff]"
                  />
                  <span className="shrink-0 font-mono text-white/35">
                    #{t.order_index}
                  </span>
                  <span className="min-w-0 truncate">{t.section_title}</span>
                  {t.latest_job_status === "done" && (
                    <span className="ml-auto shrink-0 text-[10px] text-emerald-400/80">
                      done
                    </span>
                  )}
                  {t.latest_job_status === "failed" && (
                    <span className="ml-auto shrink-0 text-[10px] text-rose-400/80">
                      failed
                    </span>
                  )}
                  {(t.latest_job_status === "running" ||
                    t.latest_job_status === "pending") && (
                    <span className="ml-auto shrink-0 text-[10px] text-amber-300/80">
                      {t.latest_job_status}
                    </span>
                  )}
                </label>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/** Compact per-role billing select (Extract / Judge). Auto follows the
 *  batch's transport; CLI/API pin that role explicitly. */
function RoleTransportSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: RoleTransport;
  onChange: (next: RoleTransport) => void;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as RoleTransport)}>
      <SelectTrigger
        className={cn(SELECT_TRIGGER, "h-9 w-[8rem] gap-1.5")}
        title={`${label} billing — Auto = follow job billing`}
      >
        <span className="shrink-0 text-[0.66rem] uppercase tracking-wide text-white/45">
          {label}
        </span>
        <SelectValue placeholder="Auto" />
      </SelectTrigger>
      <SelectContent>
        {ROLE_TRANSPORT_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** CLI | API segmented control. cli is local/free, api is pay-per-token. */
function TransportToggle({
  value,
  onChange,
}: {
  value: Transport;
  onChange: (next: Transport) => void;
}) {
  return (
    <div className="inline-flex h-9 rounded-xl border border-white/[0.1] bg-white/[0.04] p-0.5">
      {ALL_TRANSPORTS.map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => onChange(t)}
          className={cn(
            "rounded-lg px-2.5 text-xs font-medium uppercase tracking-wide transition-all",
            t === value
              ? "bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)]"
              : "text-white/55 hover:text-white",
          )}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

/** Small distinct chip flagging a billed (pay-per-token) API run. */
export function ApiBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border border-amber-400/30 bg-amber-400/15 px-1.5 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide text-amber-300",
        className,
      )}
    >
      api $
    </span>
  );
}
