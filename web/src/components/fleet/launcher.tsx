import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ListChecks,
  Loader2,
  MoreHorizontal,
  PauseCircle,
  Plus,
  PlayCircle,
  Rocket,
  RotateCcw,
  Sparkles,
  XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link, useNavigate } from "react-router-dom";
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
import { accentOf, subjectLabel, subjectLabelWithVariant } from "@/lib/subjects";
import type {
  BatchSummary,
  Book,
  NotionSubject,
  OutputLanguage,
  RoleTransport,
  SessionLimitStrategy,
  Transport,
} from "@/lib/types";
import { CARD, GHOST_BTN, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { serveability, providerServeableAnyMode } from "@/lib/serveability";
import { type LauncherConfig, loadLauncherConfig, saveLauncherConfig } from "@/lib/launcher-config";
import { LANG_LABEL, langBadge } from "@/lib/language";

const LBL = "text-xs font-medium uppercase tracking-[0.12em] text-white/45";

/** All transports a book could be launched on. cli is always available; api
 *  only for providers the backend `api_supported` map marks true. */
const ALL_TRANSPORTS: Transport[] = ["api", "cli"];

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
  // Language chosen in the Prepare form (null = UZ default, shown as "UZ" chip selected).
  const [prepLang, setPrepLang] = useState<OutputLanguage>("uz");

  const gradesQ = useQuery({
    queryKey: ["notion-grades"],
    queryFn: api.listNotionGrades,
  });
  const subjectsQ = useQuery({
    queryKey: ["notion-subjects", gradePageId],
    queryFn: () => api.listNotionSubjects(gradePageId),
    enabled: !!gradePageId,
  });
  // Available languages per subject for the selected grade. Only fetched when
  // a grade is chosen; used to enable/disable UZ/RU/EN chips in the Prepare form.
  const availLangsQ = useQuery({
    queryKey: ["notion-avail-langs", gradePageId],
    queryFn: () => api.fetchAvailableLanguages(gradePageId),
    enabled: !!gradePageId,
  });

  const pickedSubject = subjectsQ.data?.find((s) => s.page_id === subjectPageId);
  const subjectUsable = !!pickedSubject?.app_subject && !!pickedSubject?.has_textbook;

  // Available language map for the currently-picked subject (derived from availLangsQ).
  const subjectLangMap = pickedSubject?.app_subject
    ? (availLangsQ.data?.[pickedSubject.app_subject] ?? null)
    : null;

  // Reset prepLang to uz whenever subject changes (so stale selection from
  // a prior subject doesn't carry over as a disabled language).
  // (We reset on subjectPageId change via the Select onValueChange handler.)

  // When the availability map loads (or the picked subject changes), default prepLang
  // to the first language that is actually available for this subject. This prevents
  // a RU/EN-only subject from silently defaulting to a disabled UZ chip → guaranteed 422.
  useEffect(() => {
    if (!availLangsQ.data || !pickedSubject?.app_subject) return;
    const map = availLangsQ.data[pickedSubject.app_subject] ?? {};
    const preferred: OutputLanguage[] = ["uz", "ru", "en"];
    const firstAvail = preferred.find((l) => map[l]?.has_textbook);
    if (firstAvail && !map[prepLang]?.has_textbook) {
      setPrepLang(firstAvail);
    }
    // Re-run only when the map or picked subject changes; not on every prepLang change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availLangsQ.data, pickedSubject?.app_subject]);

  const prepare = useMutation({
    mutationFn: (v: { subjectPageId: string; grade: string; language: OutputLanguage }) => {
      // For non-uz languages, the per-language klass page (whose title is Cyrillic/English)
      // must be sent — NOT the UZ subject page. The UZ subject page title ("Algebra") won't
      // match the RU keyword set ("алгебра") and causes an HTTP 422.
      const pageId = subjectLangMap?.[v.language]?.page_id ?? v.subjectPageId;
      return api.fetchBookFromNotion(pageId, v.grade, v.language !== "uz" ? v.language : undefined);
    },
    onSuccess: () => {
      toast.success("Preparing — extracting lessons…");
      qc.invalidateQueries({ queryKey: ["books"] });
      // Collapse + reset the form — progress now shows in the Tray below.
      setOpen(false);
      setGradePageId("");
      setGradeDigits("");
      setSubjectPageId("");
      setPrepLang("uz");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Prepare failed"),
  });

  // ---- Tray (server-derived) ----
  // The batch key is (book_id, transport): a book can carry a cli batch AND an
  // api batch independently. Track which transports each book already has, so a
  // cli-batched book is still launchable on api (and vice-versa).
  const batchedTransports = new Map<string, Set<Transport>>();
  for (const b of batches ?? []) {
    const set = batchedTransports.get(b.book_id) ?? new Set<Transport>();
    set.add(b.transport);
    batchedTransports.set(b.book_id, set);
  }
  // Per-book Fleet lifecycle status, derived from the book's batches. A launched
  // book STAYS in the tray (it does NOT vanish once batched) — it moves from
  // "ready to launch" → "generating" → "complete" (all its homeworks done).
  const statusOf = (b: Book) => bookFleetStatus(b, batches ?? []);
  const all = books ?? [];
  const preparing = all.filter(
    (b) => b.status === "toc_extracting" || b.status === "uploading",
  );
  const failed = all.filter((b) => b.status === "failed" || b.status === "toc_review");
  // Every prepared book stays in the tray regardless of batch state; its card
  // reflects ready/generating/complete via statusOf.
  const ready = all.filter((b) => b.status === "toc_ready");
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
                setPrepLang("uz");
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
              onValueChange={(v) => { setSubjectPageId(v); setPrepLang("uz"); }}
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

        {/* Step ③ Language picker — shown after a usable subject is selected */}
        {subjectUsable && pickedSubject && (
          <div className="flex flex-col gap-1.5">
            <StepLabel n={3}>Language</StepLabel>
            <div className="flex flex-wrap gap-2">
              {(["uz", "ru", "en"] as OutputLanguage[]).map((lang) => {
                const info = subjectLangMap?.[lang];
                // If the map has loaded but this lang is absent, it's unavailable.
                const mapLoaded = availLangsQ.data != null;
                const available = !mapLoaded || (info != null && info.has_textbook);
                const selected = prepLang === lang;
                const tooltip =
                  !available && lang === "en"
                    ? "No English page yet — create an English page (with the textbook) in Notion, or upload the PDF directly."
                    : !available
                      ? `No ${LANG_LABEL[lang]} textbook available in Notion for this subject.`
                      : undefined;
                return (
                  <button
                    key={lang}
                    type="button"
                    title={tooltip}
                    disabled={!available}
                    onClick={() => available && setPrepLang(lang)}
                    className={cn(
                      "rounded-xl border px-3 py-1.5 text-xs font-medium transition-all",
                      selected
                        ? "border-[#7c5cff]/60 bg-[#7c5cff]/20 text-white shadow-[0_0_10px_-4px_rgba(124,92,255,0.6)]"
                        : available
                          ? "border-white/[0.1] bg-white/[0.04] text-white/60 hover:border-white/[0.2] hover:text-white"
                          : "cursor-not-allowed border-white/[0.06] bg-white/[0.02] text-white/25 opacity-50",
                    )}
                  >
                    {lang.toUpperCase()} — {LANG_LABEL[lang]}
                    {!available && <span className="ml-1 text-[0.6rem]">✕</span>}
                  </button>
                );
              })}
              {availLangsQ.isLoading && (
                <span className="flex items-center gap-1.5 text-xs text-white/40">
                  <Loader2 className="size-3.5 animate-spin" />
                  Checking language availability…
                </span>
              )}
            </div>
          </div>
        )}

        {/* Step ④ Confirmation line + Prepare */}
        <div className="relative flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-white/55">
            {subjectUsable && pickedSubject ? (
              <>
                Preparing{" "}
                <span className="font-medium text-white">
                  {subjectLabel(pickedSubject.app_subject ?? "")}
                  {gradeDigits && ` · Grade ${gradeDigits}`}
                  {" · "}{prepLang.toUpperCase()}
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
            onClick={() => prepare.mutate({ subjectPageId, grade: gradeDigits, language: prepLang })}
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
            countLabel={(items) => trayCountLabel(items, statusOf)}
            renderItems={(gradeBooks) => (
              // Within a grade, drill down once more by subject.
              <CategoryBrowser
                items={gradeBooks}
                getGroupKey={(b) => b.subject}
                groupLabel={subjectLabel}
                groupAccent={accentOf}
                backLabel="All subjects"
                countLabel={(items) => trayCountLabel(items, statusOf)}
                renderItems={(group) => {
                  const gPreparing = group.filter(
                    (b) =>
                      b.status === "toc_extracting" || b.status === "uploading",
                  );
                  const readyBooks = group.filter((b) => b.status === "toc_ready");
                  const gReady = readyBooks.filter((b) => statusOf(b) === "ready");
                  const gLaunched = readyBooks.filter((b) => statusOf(b) === "launched");
                  const gFailed = group.filter(
                    (b) => b.status === "failed" || b.status === "toc_review",
                  );
                  const readySection = (label: string, list: Book[]) =>
                    list.length > 0 && (
                      <div className="space-y-2">
                        <span className={LBL}>{label}</span>
                        <CardGrid>
                          {list.map((b) => (
                            <ReadyCard
                              key={b.id}
                              book={b}
                              batchedTransports={
                                batchedTransports.get(b.id) ?? new Set()
                              }
                              bookBatches={(batches ?? []).filter(
                                (bt) => bt.book_id === b.id,
                              )}
                            />
                          ))}
                        </CardGrid>
                      </div>
                    );
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

                      {readySection("Ready to launch", gReady)}
                      {readySection("Launched", gLaunched)}

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

// Group-level status (caption + sections). The books list does NOT carry
// per-lesson progress, only batches — so this can only tell "ready" (never
// launched) from "launched" (has ≥1 batch). The per-book card refines
// "launched" into generating/complete from its own TOC (see CardStatus).
type FleetStatus = "preparing" | "ready" | "launched" | "failed";

function bookFleetStatus(book: Book, batches: BatchSummary[]): FleetStatus {
  if (book.status === "toc_extracting" || book.status === "uploading") return "preparing";
  if (book.status === "failed" || book.status === "toc_review") return "failed";
  return batches.some((b) => b.book_id === book.id) ? "launched" : "ready";
}

/** Caption for a tray group, e.g. "2 ready · 3 launched". */
function trayCountLabel(items: Book[], statusOf: (b: Book) => FleetStatus): string {
  const n: Record<FleetStatus, number> = { preparing: 0, ready: 0, launched: 0, failed: 0 };
  for (const b of items) n[statusOf(b)] += 1;
  const parts: string[] = [];
  if (n.ready) parts.push(`${n.ready} ready`);
  if (n.launched) parts.push(`${n.launched} launched`);
  if (n.preparing) parts.push(`${n.preparing} preparing`);
  if (n.failed) parts.push(`${n.failed} failed`);
  return parts.join(" · ") || `${items.length} book${items.length === 1 ? "" : "s"}`;
}

// Per-book CARD status — accurate, derived from the book's own TOC
// (latest_job_status per lesson). "complete" requires EVERY lesson done.
type CardStatus = "ready" | "generating" | "complete";
const CARD_STATUS_META: Record<CardStatus, { label: string; cls: string }> = {
  ready: { label: "ready to launch", cls: "bg-sky-400/15 text-sky-200" },
  generating: { label: "generating", cls: "bg-amber-400/15 text-amber-200" },
  complete: { label: "complete", cls: "bg-emerald-400/15 text-emerald-300" },
};

function CardStatusChip({ status }: { status: CardStatus }) {
  const m = CARD_STATUS_META[status];
  return (
    <span
      className={cn(
        "rounded-md px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide",
        m.cls,
      )}
    >
      {m.label}
    </span>
  );
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
          {subjectLabelWithVariant(book.subject, book.subject_variant)}
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
  const qc = useQueryClient();
  const [retrying, setRetrying] = useState(false);

  // Re-run TOC extraction in place — see `POST /api/v1/books/<id>/toc/retry`.
  // On success the book flips back to `toc_extracting`; invalidating ["books"]
  // (same key the prepare/launch paths refresh) moves it out of the failed tray.
  async function handleRetry() {
    setRetrying(true);
    try {
      await api.retryBookToc(book.id);
      qc.invalidateQueries({ queryKey: ["books"] });
      toast.success("Re-preparing… extracting chapters");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setRetrying(false);
    }
  }

  // toc_review books need operator attention on the book page, not a silent retry.
  if (book.status === "toc_review") {
    return (
      <motion.div
        variants={fadeUpItem}
        className="flex items-start gap-3 rounded-2xl border border-amber-400/25 bg-amber-400/[0.06] p-4"
      >
        <SubjectAvatar subject={book.subject} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-medium text-white">
            {subjectLabelWithVariant(book.subject, book.subject_variant)}
            {book.grade && <GradeChip grade={book.grade} />}
          </div>
          <div className="mt-1 text-xs text-amber-300/80">
            TOC needs review — validator flagged issues.
          </div>
          <Link
            to={`/book/${book.id}`}
            className={cn(GHOST_BTN, "mt-2 inline-flex items-center gap-1.5")}
          >
            Review TOC
          </Link>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      variants={fadeUpItem}
      className="flex items-start gap-3 rounded-2xl border border-rose-500/25 bg-rose-500/[0.06] p-4"
    >
      <SubjectAvatar subject={book.subject} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-sm font-medium text-white">
          {subjectLabelWithVariant(book.subject, book.subject_variant)}
          {book.grade && <GradeChip grade={book.grade} />}
        </div>
        <div className="mt-1 text-xs text-rose-300/80">
          {book.error_message ?? "Extraction failed."}
        </div>
        <button
          type="button"
          onClick={handleRetry}
          disabled={retrying}
          className={cn(GHOST_BTN, "mt-2 inline-flex items-center gap-1.5 disabled:opacity-50")}
        >
          {retrying ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RotateCcw className="size-3.5" />
          )}
          Retry
        </button>
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
  bookBatches,
}: {
  book: Book;
  batchedTransports: Set<Transport>;
  bookBatches: BatchSummary[];
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const saved = useState(() => loadLauncherConfig(book.id))[0];
  const [provider, setProvider] = useState(() => saved.provider ?? "claude");
  const [transport, setTransport] = useState<Transport>(() => saved.transport ?? "api");
  const [sessionLimitStrategy, setSessionLimitStrategy] = useState<SessionLimitStrategy>(() => saved.sessionLimitStrategy ?? "inherit");
  const [model, setModel] = useState<string | null>(() => saved.model ?? null);
  // Output language override: null = inherit global default (not a concrete language).
  // Do NOT default to a concrete value — see launcher-role-transport-default-1 WISHLIST bug.
  const [outputLanguage, setOutputLanguage] = useState<OutputLanguage | null>(
    () => saved.outputLanguage ?? null,
  );
  const [contentSeeded, setContentSeeded] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // True while the preview fetch is in flight (launch button shows spinner).
  const [launching, setLaunching] = useState(false);

  const modelsQ = useQuery({
    queryKey: ["agent-models"],
    queryFn: api.getAgentModels,
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });
  // Fetch global launch defaults so role pickers can show "Auto → <resolved>"
  // when a role is on Auto. The query key matches what the /settings page
  // invalidates on PUT, so editing a default there immediately refreshes here.
  const defaultsQ = useQuery({
    queryKey: ["launch-defaults"],
    queryFn: api.getLaunchDefaults,
  });
  // Per-lesson completion is language-scoped: use the explicitly-picked language,
  // else the book's source language (the Auto target), then global default as last
  // resort. Keying the query on it makes switching the language picker refetch +
  // recompute the "complete"/remaining status for that language.
  const effectiveLang = outputLanguage ?? book.source_language ?? defaultsQ.data?.output_language ?? null;
  const detail = useQuery({
    queryKey: ["book", book.id, effectiveLang],
    queryFn: () => api.getBook(book.id, effectiveLang),
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
  // Accurate per-book status from the book's own TOC. "generating" requires a
  // lesson actually in flight (activeCount) — NOT merely "has a job", since
  // failed/cancelled lessons are terminal and must not pulse "generating".
  // "complete" needs EVERY lesson done; otherwise the book is launchable
  // (nothing-yet, or partial/failed → re-launch the remaining).
  const cardStatus: CardStatus = complete
    ? "complete"
    : activeCount > 0
      ? "generating"
      : "ready";
  // A plain re-launch skips done + in-flight sections and creates jobs only for
  // the rest (failed / never-run / cancelled) — that's the "remaining" count.
  const remaining = Math.max(0, (lessons ?? 0) - doneCount - activeCount);
  const subset = choosing && selected.size > 0;
  const pct = lessons && lessons > 0 ? Math.round((doneCount / lessons) * 100) : 0;
  const [from, to] = accentOf(book.subject);

  // Does the picked provider support the pay-per-token API transport? Only
  // claude/gemini do; the toggle is hidden for the rest and transport pins cli.
  const fleet = modelsQ.data?.fleet;
  const apiSupported = modelsQ.data?.api_supported?.[provider] ?? false;
  const apiFleetCheck = serveability(fleet, provider, "api");

  // Reset transport to cli whenever the provider can't do api (keeps an
  // unsupported provider from carrying a stale api selection), OR when the
  // fleet reports it can't serve api for this provider. model=null means
  // "provider default" — fine for cli, but api forces an explicit model below.
  useEffect(() => {
    if (!modelsQ.data) return; // don't sanitize against an unloaded manifest — would demote a restored api pick
    if (!apiSupported && transport === "api") {
      setTransport("cli");
      return;
    }
    if (fleet?.online && transport === "api" && !serveability(fleet, provider, "api").ok) {
      setTransport("cli");
    }
  }, [apiSupported, transport, fleet, provider, modelsQ.data]);

  // Provider reset guard: if the current provider becomes unservable (fleet
  // online + no CLI or API path), nudge to the first servable provider.
  useEffect(() => {
    if (!fleet?.online) return;
    if (providerServeableAnyMode(fleet, provider)) return;
    const firstServable = Object.keys(modelsQ.data?.providers ?? {}).find((p) =>
      providerServeableAnyMode(fleet, p),
    );
    if (firstServable) setProvider(firstServable);
  }, [fleet, provider, modelsQ.data]);

  // On api, force a concrete model: "provider default" isn't allowed (billing
  // needs an explicit model). Seed/clear the model as transport/provider change.
  const modelOptions = modelsQ.data?.providers?.[provider] ?? [];
  useEffect(() => {
    if (!modelsQ.data) return; // wait for modelOptions — else a restored api model gets nulled + re-seeded to the wrong one
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

  // One-shot: seed provider/model/transport from the global content default when
  // no per-book saved value exists and the defaults have loaded.
  useEffect(() => {
    if (contentSeeded || !defaultsQ.data) return;
    const d = defaultsQ.data;
    if (saved.provider == null && d.content_provider) setProvider(d.content_provider);
    if (saved.model == null && d.content_model) setModel(d.content_model);
    if (saved.transport == null && d.content_transport) setTransport(d.content_transport as Transport);
    setContentSeeded(true);
  }, [defaultsQ.data, contentSeeded]);

  // Persist selections per book so navigating away + back (and hard-refresh)
  // restores them. Only the launch-selection fields — never ephemeral UI state
  // (expanded / choosing / selected / launching).
  useEffect(() => {
    const cfg: LauncherConfig = {
      provider,
      transport,
      sessionLimitStrategy,
      model,
      outputLanguage,
    };
    saveLauncherConfig(book.id, cfg);
  }, [
    book.id,
    provider,
    transport,
    sessionLimitStrategy,
    model,
    outputLanguage,
  ]);

  const alreadyBatched = batchedTransports.has(transport);
  // On api we must have an explicit model selected.
  const missingApiModel = transport === "api" && !model;

  // Current batch for this transport — used for cancel-all / resume buttons.
  const currentBatch = bookBatches.find((b) => b.transport === transport) ?? null;
  const batchId = currentBatch?.batch_id ?? null;
  const rollup = currentBatch?.rollup ?? {};
  // Non-terminal = pending + running + cancelling (can be cancelled).
  const hasNonTerminal =
    ((rollup.pending ?? 0) + (rollup.running ?? 0) + (rollup.cancelling ?? 0)) > 0;
  // Failed/cancelled = resumable via resumeBatch.
  const hasFailedCancelled = ((rollup.failed ?? 0) + (rollup.cancelled ?? 0)) > 0;

  // Shared body builder for launch / preview calls.
  const launchBody = (opts: { force?: boolean; tocIds?: string[]; relaunch_mode?: "resume" | "discard" } = {}) => ({
    book_id: book.id,
    provider,
    transport,
    extract_transport: "inherit" as RoleTransport,
    judge_transport: "inherit" as RoleTransport,
    extract_provider: null,
    extract_model: null,
    judge_provider: null,
    judge_model: null,
    session_limit_strategy: sessionLimitStrategy,
    ...(transport === "api" ? { model } : {}),
    // Only send output_language when explicitly chosen — null/omitted means
    // the backend inherits the global default (same inherit convention as role transports).
    ...(outputLanguage != null ? { output_language: outputLanguage } : {}),
    ...(opts.tocIds
      ? { toc_entry_ids: opts.tocIds }
      : subset
        ? { toc_entry_ids: [...selected] }
        : {}),
    ...(opts.force ? { force: true } : {}),
    ...(opts.relaunch_mode ? { relaunch_mode: opts.relaunch_mode } : {}),
  });

  const launch = useMutation({
    mutationFn: (opts: { force?: boolean; tocIds?: string[]; relaunch_mode?: "resume" | "discard" } = {}) =>
      api.launchBatch(launchBody(opts)),
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

  const cancelAll = useMutation({
    mutationFn: () => {
      if (!batchId) return Promise.reject(new Error("No batch"));
      return api.cancelBatch(batchId);
    },
    onSuccess: (r) => {
      toast.success(`Cancelling ${r.cancelling}, cancelled ${r.cancelled}`);
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Cancel failed"),
  });

  const resumeAll = useMutation({
    mutationFn: () => {
      if (!batchId) return Promise.reject(new Error("No batch"));
      return api.resumeBatch(batchId);
    },
    onSuccess: (r) => {
      toast.success(`Resuming ${r.jobs_resumed} lessons`);
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Resume failed"),
  });

  const isBatchPaused = !!currentBatch?.paused_at;

  const pauseToggle = useMutation({
    mutationFn: () => {
      if (!batchId) return Promise.reject(new Error("No batch"));
      return isBatchPaused ? api.unpauseBatch(batchId) : api.pauseBatch(batchId);
    },
    onSuccess: (r) => {
      toast.success(r.paused ? "Batch paused" : "Batch unpaused");
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Pause toggle failed"),
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
        aria-expanded={expanded}
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-start gap-3 p-4 text-left"
      >
        <SubjectAvatar subject={book.subject} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-medium text-white">
            {subjectLabelWithVariant(book.subject, book.subject_variant)}
            {book.grade && <GradeChip grade={book.grade} />}
            <CardStatusChip status={cardStatus} />
            <span className={langBadge(book.source_language)}>
              {book.source_language.toUpperCase()}
            </span>
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
              {/* Offline banner — shown only when fleet data has arrived but no
                  worker is online. Nothing is greyed in this state (fail-open). */}
              {fleet && !fleet.online && (
                <p className="text-[0.7rem] leading-snug text-amber-300/90">
                  No workers online — launches will queue until one connects.
                </p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Select value={provider} onValueChange={setProvider}>
                  <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[8.5rem]")}>
                    <SelectValue placeholder="claude" />
                  </SelectTrigger>
                  <SelectContent>
                    {(providers.length > 0 ? providers : ["claude"]).map((p) => {
                      const serveable = providerServeableAnyMode(fleet, p);
                      return (
                        <SelectItem key={p} value={p} disabled={!serveable}>
                          {serveable ? p : `${p} — no worker runs it`}
                        </SelectItem>
                      );
                    })}
                  </SelectContent>
                </Select>
                {/* CLI | API toggle — only for providers the backend bills via API.
                    API side additionally requires the fleet to have credentials. */}
                {apiSupported && (
                  <TransportToggle
                    value={transport}
                    onChange={setTransport}
                    apiDisabled={!apiFleetCheck.ok}
                    apiDisabledReason={!apiFleetCheck.ok ? apiFleetCheck.reason : null}
                  />
                )}
                {/* Session-limit strategy — what to do when a Claude session limit hits. */}
                <div className="flex flex-col gap-1">
                  <span className="text-[0.6rem] font-medium uppercase tracking-[0.12em] text-white/35">
                    On limit
                  </span>
                  <Select
                    value={sessionLimitStrategy}
                    onValueChange={(v) => setSessionLimitStrategy(v as SessionLimitStrategy)}
                  >
                    <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[7rem]")}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">Auto</SelectItem>
                      <SelectItem value="pause">Pause</SelectItem>
                      <SelectItem value="switch">Switch</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {/* Output language — null = Auto (= book's source language). */}
                <div className="flex flex-col gap-1">
                  <span className="text-[0.6rem] font-medium uppercase tracking-[0.12em] text-white/35">
                    Language
                  </span>
                  <Select
                    value={outputLanguage ?? "inherit"}
                    onValueChange={(v) =>
                      setOutputLanguage(v === "inherit" ? null : (v as OutputLanguage))
                    }
                  >
                    <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[9rem]")}>
                      {outputLanguage == null ? (
                        <SelectValue>
                          {`Auto → ${LANG_LABEL[book.source_language]}`}
                        </SelectValue>
                      ) : (
                        <SelectValue />
                      )}
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">
                        {`Auto → ${LANG_LABEL[book.source_language]}`}
                      </SelectItem>
                      <SelectItem value="uz">UZ — O'zbek</SelectItem>
                      <SelectItem value="en">EN — English</SelectItem>
                      <SelectItem value="ru">RU — Русский</SelectItem>
                    </SelectContent>
                  </Select>
                  {/* Translate hint — shown when operator picks a language that
                      differs from the textbook's own source language. */}
                  {outputLanguage != null && outputLanguage !== book.source_language && (
                    <span className="text-[0.65rem] text-amber-300/80">
                      ↳ translate from {book.source_language.toUpperCase()}
                    </span>
                  )}
                </div>
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

                {/* Cancel-all — visible when there are pending/running jobs in this batch. */}
                {batchId && hasNonTerminal && !choosing && (
                  <button
                    type="button"
                    className={cn(GHOST_BTN, "h-9 px-2.5 text-xs text-rose-300/80 hover:text-rose-200")}
                    disabled={cancelAll.isPending}
                    title="Cancel all pending and running lessons in this batch"
                    onClick={() => cancelAll.mutate()}
                  >
                    {cancelAll.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <XCircle className="size-3.5" />
                    )}
                    Cancel all
                  </button>
                )}

                {/* Pause/Resume — visible when batch exists and has non-terminal jobs or is paused. */}
                {batchId && (hasNonTerminal || isBatchPaused) && !choosing && (
                  <button
                    type="button"
                    className={cn(GHOST_BTN, "h-9 px-2.5 text-xs text-amber-300/80 hover:text-amber-200")}
                    disabled={pauseToggle.isPending}
                    title={isBatchPaused ? "Unpause this batch" : "Pause this batch (stops new jobs from starting)"}
                    onClick={() => pauseToggle.mutate()}
                  >
                    {pauseToggle.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : isBatchPaused ? (
                      <PlayCircle className="size-3.5" />
                    ) : (
                      <PauseCircle className="size-3.5" />
                    )}
                    {isBatchPaused ? "Unpause" : "Pause"}
                  </button>
                )}

                {/* Resume failed/cancelled — visible when there are failed/cancelled jobs. */}
                {batchId && hasFailedCancelled && !choosing && (
                  <button
                    type="button"
                    className={cn(GHOST_BTN, "h-9 px-2.5 text-xs")}
                    disabled={resumeAll.isPending}
                    title="Resume all failed/cancelled lessons (reuses saved phases)"
                    onClick={() => resumeAll.mutate()}
                  >
                    {resumeAll.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <RotateCcw className="size-3.5" />
                    )}
                    Resume failed
                  </button>
                )}

                {/* Re-run all → kebab overflow (destructive, re-bills ALL). */}
                {alreadyBatched && !choosing && (
                  <details className="group relative">
                    <summary
                      className={cn(
                        GHOST_BTN,
                        "h-9 cursor-pointer list-none px-2.5 text-xs select-none",
                      )}
                      title="More batch actions"
                    >
                      <MoreHorizontal className="size-3.5" />
                    </summary>
                    {/* Dropdown panel */}
                    <div className="absolute left-0 top-full z-10 mt-1 w-52 rounded-xl border border-white/[0.1] bg-[#1a1630] shadow-xl">
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-xs text-rose-300/90 hover:bg-white/[0.06] hover:text-rose-200 disabled:opacity-50"
                        disabled={launch.isPending || missingApiModel}
                        title={
                          missingApiModel
                            ? "Pick a model to launch on API"
                            : "Regenerate every lesson, discarding completed outputs"
                        }
                        onClick={(e) => {
                          // Close the <details> before the confirm so it doesn't
                          // stay open if the user cancels.
                          (e.currentTarget.closest("details") as HTMLDetailsElement | null)?.removeAttribute("open");
                          if (
                            window.confirm(
                              `Regenerate ALL ${lessons ?? ""} lessons, including completed ones? This discards finished outputs and re-bills all ${lessons ?? ""}.`,
                            )
                          )
                            launch.mutate({ force: true });
                        }}
                      >
                        <RotateCcw className="size-3.5 shrink-0" />
                        Discard &amp; re-run all
                      </button>
                    </div>
                  </details>
                )}

                {/* Primary: Launch remaining (or Launch all for fresh batch).
                    For a "choosing" subset, launches the selected lessons only.
                    When there are remaining lessons, previews first to check for
                    saved work → dialog for resume vs discard. */}
                <button
                  type="button"
                  className={cn(PRIMARY_BTN, "ml-auto")}
                  disabled={
                    launch.isPending ||
                    launching ||
                    (choosing && selected.size === 0) ||
                    missingApiModel ||
                    (!choosing && lessons != null && remaining === 0)
                  }
                  title={
                    missingApiModel
                      ? "Pick a model to launch on API"
                      : complete
                        ? "All lessons done — use ⋯ Re-run all to regenerate"
                        : !choosing && remaining === 0
                          ? "All remaining lessons are in progress"
                          : undefined
                  }
                  onClick={async () => {
                    if (choosing) {
                      // Choosing mode: launch selected — no preview needed
                      // (all selected are non-done by definition).
                      launch.mutate({});
                      return;
                    }
                    setLaunching(true);
                    try {
                      const p = await api.previewBatch(launchBody());
                      if (p.resumable > 0) {
                        // Some remaining lessons have saved phases.
                        // Three-way: Resume (OK) → Discard (second confirm OK) → bail.
                        const resume = window.confirm(
                          `${p.resumable} of these lessons have saved work.\n\n` +
                          `OK = Resume them (reuse saved phases, only unfinished re-run).\n` +
                          `Cancel = choose Discard instead.`,
                        );
                        if (resume) {
                          // Primary: resume saved work
                          launch.mutate({ relaunch_mode: "resume" });
                        } else {
                          // Secondary escape hatch: offer discard & regenerate
                          const discard = window.confirm(
                            `Discard saved work on ${p.resumable} lesson(s) and regenerate from scratch? ` +
                            `This re-bills ${p.resumable}.`,
                          );
                          if (discard) launch.mutate({ relaunch_mode: "discard" });
                          // else: do nothing
                        }
                      } else {
                        // Nothing saved at stake → straight launch
                        launch.mutate({});
                      }
                    } catch (e) {
                      toast.error(e instanceof Error ? e.message : "Preview failed");
                    } finally {
                      setLaunching(false);
                    }
                  }}
                >
                  {(launch.isPending || launching) ? (
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

/** CLI | API segmented control. cli is local/free, api is pay-per-token.
 *  `apiDisabled` disables the API button (fleet has no creds for this provider);
 *  `apiDisabledReason` is shown as an amber helper line beneath the toggle. */
function TransportToggle({
  value,
  onChange,
  apiDisabled,
  apiDisabledReason,
}: {
  value: Transport;
  onChange: (next: Transport) => void;
  apiDisabled?: boolean;
  apiDisabledReason?: string | null;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="inline-flex h-9 rounded-xl border border-white/[0.1] bg-white/[0.04] p-0.5">
        {ALL_TRANSPORTS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => { if (t !== "api" || !apiDisabled) onChange(t); }}
            disabled={t === "api" && !!apiDisabled}
            className={cn(
              "rounded-lg px-2.5 text-xs font-medium uppercase tracking-wide transition-all",
              t === value
                ? "bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)]"
                : "text-white/55 hover:text-white",
              t === "api" && apiDisabled && "cursor-not-allowed opacity-40",
            )}
          >
            {t}
          </button>
        ))}
      </div>
      {apiDisabledReason && (
        <p className="text-[0.7rem] leading-snug text-amber-300/90">{apiDisabledReason}</p>
      )}
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
