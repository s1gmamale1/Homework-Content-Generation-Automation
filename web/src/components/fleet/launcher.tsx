import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ListChecks,
  Loader2,
  Plus,
  Rocket,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import {
  CategoryBrowser,
  compareGradeGroups,
  gradeAccent,
  gradeBadge,
  gradeKey,
  gradeLabel,
} from "@/components/category-browser";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { fadeUpItem, staggerContainer } from "@/lib/motion";
import { accentOf, subjectLabel } from "@/lib/subjects";
import type {
  BatchSummary,
  Book,
  NotionSubject,
  RoleTransport,
  Transport,
} from "@/lib/types";
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

  const [open, setOpen] = useState(false);
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
      // Collapse + reset the form — progress now shows in the Tray below.
      setOpen(false);
      setGradePageId("");
      setGradeDigits("");
      setSubjectPageId("");
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
  // Union of every tray-relevant book, fed to the category drill-down. Each
  // subject group is re-split back into Preparing/Ready/Failed on render.
  const trayBooks = [...preparing, ...ready, ...failed];
  const trayEmpty = trayBooks.length === 0;

  return (
    <>
      {/* Part A — Prepare (its own card, guided steps) */}
      <div className={cn(CARD, "relative overflow-hidden")}>
        {/* Soft accent glow — gives the card some life instead of a flat slab. */}
        <div
          aria-hidden
          className="pointer-events-none absolute -right-16 -top-24 size-52 rounded-full bg-[#7c5cff]/20 blur-3xl"
        />
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="relative flex w-full items-center gap-3.5 text-left"
        >
          <span className="grid size-11 shrink-0 place-items-center rounded-2xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30 shadow-[0_14px_30px_-14px_rgba(124,92,255,0.8)]">
            <Sparkles className="size-5 text-white" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold tracking-tight text-white">
              Prepare a subject
            </h2>
            <p className="mt-1 text-xs leading-5 text-white/50">
              {open
                ? "Pick a grade and a subject, then start the extraction."
                : "Pull a textbook from Notion, extract its lessons, then launch it across the fleet."}
            </p>
          </div>
          <span
            className={cn(
              "grid size-10 shrink-0 place-items-center rounded-full border border-white/[0.14] bg-gradient-to-br from-[#7c5cff] to-[#4d8dff] text-white shadow-[0_12px_30px_-12px_rgba(124,92,255,0.9)] transition-transform duration-300 active:scale-95",
              open && "rotate-[135deg]",
            )}
          >
            <Plus className="size-5" />
          </span>
        </button>

        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="prepare-form"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
              className="overflow-hidden"
            >
              <div className="space-y-5 pt-5">
        <div className="relative grid gap-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4 sm:grid-cols-2">
          {/* Step ① Grade */}
          <div className="flex flex-col gap-1.5">
            <StepLabel n={1}>Grade</StepLabel>
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

          {/* Step ② Subject — dimmed/disabled until a grade is chosen */}
          <div
            className={cn(
              "flex flex-col gap-1.5 transition-opacity",
              !gradePageId && "pointer-events-none opacity-40",
            )}
          >
            <StepLabel n={2}>Subject</StepLabel>
            <Select
              value={subjectPageId}
              onValueChange={setSubjectPageId}
              disabled={!gradePageId || subjectsQ.isLoading}
            >
              <SelectTrigger className={SELECT_TRIGGER}>
                <SelectValue
                  placeholder={
                    !gradePageId
                      ? "Choose a grade first"
                      : subjectsQ.isLoading
                        ? "Loading subjects…"
                        : "Choose a subject"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(subjectsQ.data ?? []).map((s) => (
                  <SelectItem
                    key={s.page_id}
                    value={s.page_id}
                    disabled={!s.has_textbook || !s.app_subject}
                  >
                    <span className="flex items-center gap-2">
                      <span>{s.notion_title}</span>
                      <SubjectBadge subject={s} />
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Step ③ Confirmation line + Prepare */}
        <div className="relative flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-white/55">
            {subjectUsable && pickedSubject ? (
              <>
                Preparing{" "}
                <span className="font-medium text-white">
                  {subjectLabel(pickedSubject.app_subject ?? "")}
                  {gradeDigits && ` · Grade ${gradeDigits}`}
                </span>{" "}
                — extracts the table of contents (~1–3 min).
              </>
            ) : (
              <span className="text-white/35">
                Pick a grade and a subject with a textbook to continue.
              </span>
            )}
          </p>
          <button
            type="button"
            className={cn(PRIMARY_BTN, "shrink-0")}
            disabled={!subjectUsable || prepare.isPending}
            onClick={() => prepare.mutate({ subjectPageId, grade: gradeDigits })}
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
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Part B — Tray (separate card) */}
      <div className={cn(CARD, "space-y-4")}>
        <h3 className="text-sm font-semibold tracking-tight text-white">Tray</h3>

        {trayEmpty ? (
          <p className="text-sm text-white/45">Prepare a subject above to get started.</p>
        ) : (
          <CategoryBrowser
            items={trayBooks}
            getGroupKey={(b) => gradeKey(b.grade)}
            groupLabel={gradeLabel}
            groupAccent={gradeAccent}
            groupBadge={gradeBadge}
            sortGroups={compareGradeGroups}
            backLabel="All grades"
            countLabel={(items) => trayCountLabel(items)}
            renderItems={(gradeBooks) => (
              // Within a grade, drill down once more by subject.
              <CategoryBrowser
                items={gradeBooks}
                getGroupKey={(b) => b.subject}
                groupLabel={subjectLabel}
                groupAccent={accentOf}
                backLabel="All subjects"
                countLabel={(items) => trayCountLabel(items)}
                renderItems={(group) => {
                  const gPreparing = group.filter(
                    (b) =>
                      b.status === "toc_extracting" || b.status === "uploading",
                  );
                  const gReady = group.filter((b) => b.status === "toc_ready");
                  const gFailed = group.filter((b) => b.status === "failed");
                  return (
                    <div className="space-y-5">
                      {gPreparing.length > 0 && (
                        <div className="space-y-2">
                          <span className={LBL}>Preparing</span>
                          <CardGrid>
                            {gPreparing.map((b) => (
                              <PreparingCard key={b.id} book={b} />
                            ))}
                          </CardGrid>
                        </div>
                      )}

                      {gReady.length > 0 && (
                        <div className="space-y-2">
                          <span className={LBL}>Ready</span>
                          <CardGrid>
                            {gReady.map((b) => (
                              <ReadyCard
                                key={b.id}
                                book={b}
                                batchedTransports={
                                  batchedTransports.get(b.id) ?? new Set()
                                }
                              />
                            ))}
                          </CardGrid>
                        </div>
                      )}

                      {gFailed.length > 0 && (
                        <div className="space-y-2">
                          <span className={LBL}>Failed</span>
                          <CardGrid>
                            {gFailed.map((b) => (
                              <FailedCard key={b.id} book={b} />
                            ))}
                          </CardGrid>
                        </div>
                      )}
                    </div>
                  );
                }}
              />
            )}
          />
        )}
      </div>
    </>
  );
}

/* ── Tray scaffolding ───────────────────────────────────────────────── */

/** Staggered responsive grid — mirrors the Library card grid breakpoints.
 *  `items-start` keeps an expanded Ready card from stretching its row-mates. */
function CardGrid({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      className="grid grid-cols-1 items-start gap-3 sm:grid-cols-2 xl:grid-cols-3"
      variants={staggerContainer}
      initial="hidden"
      animate="show"
    >
      {children}
    </motion.div>
  );
}

/** Subject-accent gradient avatar (first letter) — same treatment as Library. */
function SubjectAvatar({ subject }: { subject: string }) {
  const [from, to] = accentOf(subject);
  return (
    <span
      className="grid size-10 shrink-0 place-items-center rounded-xl text-sm font-bold text-[#16131f]"
      style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
    >
      {subjectLabel(subject).charAt(0)}
    </span>
  );
}

/** Status-aware caption for a subject's tray group, e.g. "2 ready · 1 failed".
 *  Falls back to a plain book count if only one bucket is present. */
function trayCountLabel(items: Book[]): string {
  const preparing = items.filter(
    (b) => b.status === "toc_extracting" || b.status === "uploading",
  ).length;
  const ready = items.filter((b) => b.status === "toc_ready").length;
  const failed = items.filter((b) => b.status === "failed").length;
  const parts: string[] = [];
  if (ready) parts.push(`${ready} ready`);
  if (preparing) parts.push(`${preparing} preparing`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(" · ") || `${items.length} book${items.length === 1 ? "" : "s"}`;
}

function GradeChip({ grade }: { grade: string }) {
  return (
    <span className="rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-normal text-white/55">
      Grade {grade}
    </span>
  );
}

/* ── Preparing / Failed cards ───────────────────────────────────────── */

function PreparingCard({ book }: { book: Book }) {
  return (
    <motion.div
      variants={fadeUpItem}
      className="flex items-start gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4"
    >
      <SubjectAvatar subject={book.subject} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-sm font-medium text-white">
          {subjectLabel(book.subject)}
          {book.grade && <GradeChip grade={book.grade} />}
        </div>
        <div className="mt-1 flex items-center gap-1.5 text-xs text-white/45">
          <Loader2 className="size-3.5 animate-spin text-[#5b8dff]" />
          extracting lessons… ~1–3 min
        </div>
      </div>
    </motion.div>
  );
}

function FailedCard({ book }: { book: Book }) {
  return (
    <motion.div
      variants={fadeUpItem}
      className="flex items-start gap-3 rounded-2xl border border-rose-500/25 bg-rose-500/[0.06] p-4"
    >
      <SubjectAvatar subject={book.subject} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-sm font-medium text-white">
          {subjectLabel(book.subject)}
          {book.grade && <GradeChip grade={book.grade} />}
        </div>
        <div className="mt-1 text-xs text-rose-300/80">
          {book.error_message ?? "Extraction failed."}
        </div>
        <div className="mt-1 text-[0.7rem] text-white/35">
          Re-prepare the subject above to try again.
        </div>
      </div>
    </motion.div>
  );
}

/* ── Subject availability badge (Prepare step ②) ────────────────────── */

function SubjectBadge({ subject }: { subject: NotionSubject }) {
  if (!subject.has_textbook) {
    return (
      <span className="rounded-md bg-amber-400/15 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide text-amber-200">
        no textbook
      </span>
    );
  }
  if (!subject.app_subject) {
    return (
      <span className="rounded-md bg-white/[0.08] px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide text-white/45">
        unsupported
      </span>
    );
  }
  return (
    <span className="rounded-md bg-emerald-400/15 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide text-emerald-300">
      textbook ready
    </span>
  );
}

/* ── Ready card — collapsed summary, click to reveal launch controls ──── */

function ReadyCard({
  book,
  batchedTransports,
}: {
  book: Book;
  batchedTransports: Set<Transport>;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
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
  const activeCount = toc.filter(
    (t) =>
      t.latest_job_status === "running" ||
      t.latest_job_status === "pending" ||
      t.latest_job_status === "cancelling",
  ).length;
  const complete = lessons != null && lessons > 0 && doneCount === lessons;
  // A plain re-launch skips done + in-flight sections and creates jobs only for
  // the rest (failed / never-run / cancelled) — that's the "remaining" count.
  const remaining = Math.max(0, (lessons ?? 0) - doneCount - activeCount);
  const subset = choosing && selected.size > 0;
  const pct = lessons && lessons > 0 ? Math.round((doneCount / lessons) * 100) : 0;
  const [from, to] = accentOf(book.subject);

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
    mutationFn: (opts: { force?: boolean; tocIds?: string[] } = {}) =>
      api.launchBatch({
        book_id: book.id,
        provider,
        transport,
        extract_transport: extractTransport,
        judge_transport: judgeTransport,
        ...(transport === "api" ? { model } : {}),
        ...(opts.tocIds
          ? { toc_entry_ids: opts.tocIds }
          : subset
            ? { toc_entry_ids: [...selected] }
            : {}),
        ...(opts.force ? { force: true } : {}),
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
    <motion.div
      variants={fadeUpItem}
      className={cn(
        "overflow-hidden rounded-2xl border bg-white/[0.04] shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-colors",
        expanded ? "border-white/[0.16]" : "border-white/[0.08] hover:border-white/[0.12]",
      )}
    >
      {/* Collapsed summary — the whole header toggles the control panel. */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-start gap-3 p-4 text-left"
      >
        <SubjectAvatar subject={book.subject} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-medium text-white">
            {subjectLabel(book.subject)}
            {book.grade && <GradeChip grade={book.grade} />}
          </div>
          <div className="mt-0.5 text-xs text-white/45">
            {lessons ?? "…"} lessons
            {doneCount > 0 && (
              <span className={complete ? "text-emerald-400/80" : undefined}>
                {" · "}
                {complete ? "complete" : `${doneCount}/${lessons} done`}
              </span>
            )}
          </div>
          {/* Progress bar — only once some lessons have run. */}
          {doneCount > 0 && (
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${pct}%`,
                  background: `linear-gradient(90deg, ${from}, ${to})`,
                }}
              />
            </div>
          )}
        </div>
        <ChevronDown
          className={cn(
            "mt-1 size-4 shrink-0 text-white/40 transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {/* Expanded controls — provider/transport/extract/judge/model + launch. */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="space-y-3 border-t border-white/[0.07] px-4 pb-4 pt-3">
              <div className="flex flex-wrap items-center gap-2">
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
              </div>

              <div className="flex flex-wrap items-center gap-2">
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
                {/* Re-run the whole batch (force) — regenerates done lessons too.
                    Only when this transport already has a batch and not mid-select. */}
                {alreadyBatched && !choosing && (
                  <button
                    type="button"
                    className={cn(GHOST_BTN, "h-9 px-2.5 text-xs")}
                    disabled={launch.isPending || missingApiModel}
                    title={
                      missingApiModel
                        ? "Pick a model to launch on API"
                        : "Re-generate every lesson in this batch (discards completed outputs)"
                    }
                    onClick={() => {
                      if (
                        window.confirm(
                          `Re-generate ALL ${lessons ?? ""} lessons in this batch? This regenerates completed lessons too${transport === "api" ? " and bills API calls" : ""}.`,
                        )
                      )
                        launch.mutate({ force: true });
                    }}
                  >
                    <RotateCcw className="size-3.5" />
                    Re-run all
                  </button>
                )}
                <button
                  type="button"
                  className={cn(PRIMARY_BTN, "ml-auto")}
                  disabled={
                    launch.isPending ||
                    (choosing && selected.size === 0) ||
                    missingApiModel ||
                    (!choosing && lessons != null && remaining === 0)
                  }
                  title={
                    missingApiModel
                      ? "Pick a model to launch on API"
                      : complete
                        ? "All lessons done — use Re-run all to regenerate"
                        : !choosing && remaining === 0
                          ? "All remaining lessons are in progress"
                          : undefined
                  }
                  onClick={() => launch.mutate({})}
                >
                  {launch.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Rocket className="size-4" />
                  )}
                  {choosing
                    ? `Launch ${selected.size}`
                    : lessons == null
                      ? "Launch"
                      : complete
                        ? "Complete"
                        : remaining === 0
                          ? "In progress"
                          : doneCount > 0 || activeCount > 0
                            ? `Launch remaining ${remaining}`
                            : "Launch"}
                </button>
              </div>

              {choosing && (
                <div className="w-full space-y-1 rounded-xl border border-white/[0.08] bg-black/20 p-2">
                  {toc.length === 0 ? (
                    <div className="px-1 py-1 text-xs text-white/45">No lessons found.</div>
                  ) : (
                    toc.map((t) => {
                      // Done lessons are NOT selectable for a normal launch — they're
                      // already generated. Re-running one is an explicit Retry (force).
                      if (t.latest_job_status === "done") {
                        return (
                          <div
                            key={t.id}
                            className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-xs text-white/45"
                          >
                            <span className="grid size-3.5 shrink-0 place-items-center text-emerald-400/80">
                              <Check className="size-3" />
                            </span>
                            <span className="shrink-0 font-mono text-white/25">
                              #{t.order_index}
                            </span>
                            <span className="min-w-0 truncate">{t.section_title}</span>
                            <span className="ml-auto shrink-0 text-[10px] text-emerald-400/80">
                              done
                            </span>
                            <button
                              type="button"
                              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/[0.1] px-1.5 py-0.5 text-[10px] text-white/60 transition-colors hover:bg-white/[0.06] hover:text-white disabled:opacity-40"
                              disabled={launch.isPending || missingApiModel}
                              title={
                                missingApiModel
                                  ? "Pick a model to launch on API"
                                  : "Re-generate just this lesson (discards its current output)"
                              }
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `Re-generate "${t.section_title}"? This discards its current output and runs it again${transport === "api" ? " (billed API call)" : ""}.`,
                                  )
                                )
                                  launch.mutate({ force: true, tocIds: [t.id] });
                              }}
                            >
                              <RotateCcw className="size-3" />
                              Retry
                            </button>
                          </div>
                        );
                      }
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
                          {t.latest_job_status === "failed" && (
                            <span className="ml-auto shrink-0 text-[10px] text-rose-400/80">
                              failed
                            </span>
                          )}
                          {(t.latest_job_status === "running" ||
                            t.latest_job_status === "pending" ||
                            t.latest_job_status === "cancelling") && (
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
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/** Compact step label with a numbered marker for the Prepare flow. */
function StepLabel({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <span className={cn(LBL, "flex items-center gap-1.5")}>
      <span className="grid size-4 place-items-center rounded-full bg-white/[0.08] text-[0.6rem] font-bold text-white/70">
        {n}
      </span>
      {children}
    </span>
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
