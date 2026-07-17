import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  FileText,
  Library,
  Loader2,
  Upload as UploadIcon,
} from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { PrepareStatusPanel } from "@/components/notion/prepare-status-panel";
import { SpaceBackdrop } from "@/components/space-backdrop";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { LANG_LABEL } from "@/lib/language";
import { fadeUpItem, staggerContainer, tapScale } from "@/lib/motion";
import {
  langChipState,
  partForResolution,
  resolveCandidate,
  resolveNotionPageId,
} from "@/lib/notion-parts";
import {
  candidatePrepareStatus,
  hasMidFlightBook,
  partPrepareStatus,
  proceedBlockedTooltip,
  resolvedPrepareStatus,
} from "@/lib/prepare-status";
import { subjectLabel } from "@/lib/subjects";
import {
  type NotionCandidate,
  type NotionGrade,
  type NotionSubject,
  type OutputLanguage,
  SUBJECTS,
  type Subject,
} from "@/lib/types";
import { GHOST_BTN, GLASS_BTN, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";

/** Pending file-level pick when a resolved part carries >1 candidate in its
 *  best rank tier (BE-19 task 6) — the operator must choose which file
 *  before the fetch proceeds. */
interface CandidatePick {
  subject: NotionSubject;
  language: OutputLanguage;
  // The OWNING PART's page_id (not any candidate's) — this is what must be
  // submitted as subject_page_id to /from-notion. A child-page candidate's
  // own page_id fails backend ancestry validation (its direct parent is the
  // subject page, not the language container) — BE-19 final-review critical fix.
  partPageId: string;
  candidates: NotionCandidate[];
  selected: NotionCandidate | null;
}

const GRADES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"];
const LBL = "text-sm font-medium text-white/75";

export function UploadPage() {
  const navigate = useNavigate();
  const [source, setSource] = useState<"choose" | "upload" | "notion">("choose");
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState<Subject | "">("");
  const [grade, setGrade] = useState("");
  // Source language for direct upload (the textbook's own language). Default uz.
  const [sourceLanguage, setSourceLanguage] = useState<OutputLanguage>("uz");
  const [busy, setBusy] = useState(false);

  const [nGrade, setNGrade] = useState("");
  const [nGradePageId, setNGradePageId] = useState("");
  const [grades, setGrades] = useState<NotionGrade[] | null>(null);
  const [subjects, setSubjects] = useState<NotionSubject[] | null>(null);
  const [pendingSubjectId, setPendingSubjectId] = useState<string | null>(null);
  const [nErr, setNErr] = useState<string | null>(null);
  // Set when the resolved part has >1 candidate file in its best rank tier —
  // the fetch is held until the operator picks one.
  const [candidatePick, setCandidatePick] = useState<CandidatePick | null>(null);

  // Available language containers per app_subject for the picked Notion grade —
  // also carries each part's system state (task 4: prepared/preparing/needs
  // review/failed) that drives the PREPARED/… chips below. Polled while a
  // Notion grade is picked AND a linked part is still mid-flight, so leaving
  // and reopening this page (or just waiting) reflects reality instead of a
  // stale one-shot fetch — same enabled-gated pattern as BatchLessonList.
  const availLangsQ = useQuery({
    queryKey: ["notion-avail-langs", nGradePageId],
    queryFn: () => api.fetchAvailableLanguages(nGradePageId),
    enabled: source === "notion" && !!nGradePageId,
    refetchInterval: (query) =>
      source === "notion" && hasMidFlightBook(query.state.data) ? 4000 : false,
  });
  const availLangs = availLangsQ.data ?? null;

  const onDrop = useCallback((accepted: File[]) => {
    if (accepted[0]) setFile(accepted[0]);
  }, []);

  const dz = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 1,
    multiple: false,
    disabled: busy,
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !subject) {
      toast.error("Pick a subject and a PDF first.");
      return;
    }
    setBusy(true);
    try {
      const book = await api.uploadBook(file, subject as Subject, grade || undefined, sourceLanguage);
      toast.success(
        book.deduplicated ? "This book already exists — reusing it." : "Uploaded.",
      );
      navigate(`/book/${book.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      toast.error(msg);
      setBusy(false);
    }
  }

  async function enterNotion() {
    setSource("notion");
    setNErr(null);
    setSubjects(null);
    try {
      setGrades(await api.listNotionGrades());
    } catch (e) {
      setNErr(e instanceof Error ? e.message : "Notion unavailable");
    }
  }

  async function pickGrade(gradePageId: string, gradeTitle: string) {
    setNGrade(gradeTitle.replace(/\D/g, ""));
    setNGradePageId(gradePageId);
    setSubjects(null);
    setPendingSubjectId(null);
    setCandidatePick(null);
    setNErr(null);
    try {
      // Available-languages is fetched by availLangsQ (react-query, keyed on
      // nGradePageId above) — only the subject list is imperative here.
      setSubjects(await api.listNotionSubjects(gradePageId));
    } catch (e) {
      setNErr(e instanceof Error ? e.message : "Could not load subjects");
    }
  }

  async function pickSubject(s: NotionSubject, language: OutputLanguage) {
    if (!s.app_subject || !s.has_textbook) return;
    if (busy) return; // guard against a second fetch while one is in flight
    setCandidatePick(null);
    // resolveNotionPageId keeps the clicked UZ page authoritative for UZ output
    // and translates cross-language only when a single textbook part exists
    // (notion-multipart-subject-clobber-1). Multi-part chips are disabled below,
    // so a null here is a defensive guard.
    const langMap = s.app_subject ? (availLangs?.[s.app_subject] ?? null) : null;
    const pageId = resolveNotionPageId(s.page_id, language, langMap);
    if (pageId == null) {
      toast.error("This language has multiple textbook parts — pick a specific part or upload the PDF directly.");
      return;
    }
    // File-level disambiguation layer (BE-19 task 6): the resolved part may
    // itself carry >1 candidate PDF in its best rank tier (e.g. two files
    // that both look like the textbook). Hold the fetch until the operator
    // picks one; a single best-tier candidate auto-resolves below.
    const part = partForResolution(s.page_id, language, langMap);
    // Defense-in-depth (PR #99 gate finding 2): the language button is
    // already disabled once this resolves to a linked, non-proceed state —
    // never let a stray click re-fire /from-notion on an already-tracked
    // book. A part with an ambiguous (>1 candidate) rollup still falls back
    // to the conservative `textbook_ready` proceed:true here, so this never
    // blocks entering the file picker below — only a resolved single part
    // that's actually prepared/preparing/needs-review/failed is stopped.
    if (!partPrepareStatus(part).actions.proceed) return;
    const resolution = resolveCandidate(part);
    if (resolution.status === "none" || !part) {
      toast.error("No textbook file found for this language — upload the PDF directly.");
      return;
    }
    if (resolution.status === "ambiguous") {
      // The owning PART's page_id (not any candidate's) is what must be
      // submitted to /from-notion — a child-page candidate's own page_id
      // fails backend ancestry validation (BE-19 final-review critical fix).
      // Stash it now so the "Fetch selected" handler below never needs to
      // re-derive `part` from stale state.
      setCandidatePick({ subject: s, language, partPageId: part.page_id, candidates: resolution.candidates, selected: null });
      return;
    }
    await runFetch(s, language, resolution.page_id, resolution.block_id);
  }

  async function runFetch(s: NotionSubject, language: OutputLanguage, pageId: string, blockId: string) {
    setPendingSubjectId(s.page_id);
    setBusy(true);
    try {
      const book = await api.fetchBookFromNotion(
        pageId,
        nGrade,
        language !== "uz" ? language : undefined,
        blockId,
      );
      for (const w of book.warnings ?? []) toast.warning(w);
      toast.success("Fetched.");
      navigate(`/book/${book.id}`);
    } catch (e) {
      // A stale-crawl ambiguous_textbook 422 lands here too — the message is
      // already extracted from the dict-shaped detail by api.ts; the picker
      // self-heals on the next available-languages refresh.
      toast.error(e instanceof Error ? e.message : "Fetch failed");
      setBusy(false);
      setPendingSubjectId(null);
    } finally {
      setCandidatePick(null);
    }
  }

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10">
        <span className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.16em] text-white/45">
          New session
        </span>

        {source === "choose" && (
          <>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Start a new session
            </h1>
            <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-white/55">
              Fetch a curriculum book straight from Notion, or upload your own PDF. Either way the
              system extracts the table of contents and assembles a homework packet aligned to the
              source material.
            </p>

            <motion.div
              className="mt-8 grid gap-4 sm:grid-cols-2"
              variants={staggerContainer}
              initial="hidden"
              animate="show"
            >
              <motion.button
                type="button"
                onClick={enterNotion}
                variants={fadeUpItem}
                whileTap={tapScale}
                className="flex flex-col items-start gap-2 rounded-2xl border border-white/[0.09] bg-white/[0.04] px-5 py-6 text-left shadow-[0_18px_50px_-40px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-colors hover:border-white/[0.16] hover:bg-white/[0.06]"
              >
                <span className="grid size-11 place-items-center rounded-xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30">
                  <Library className="size-5 text-white" />
                </span>
                <span className="mt-1 text-base font-semibold text-white">Fetch From Notion</span>
                <span className="text-sm text-white/55">
                  Pick a grade and subject; the textbook is pulled from the Notion library.
                </span>
              </motion.button>

              <motion.button
                type="button"
                onClick={() => setSource("upload")}
                variants={fadeUpItem}
                whileTap={tapScale}
                className="flex flex-col items-start gap-2 rounded-2xl border border-white/[0.09] bg-white/[0.04] px-5 py-6 text-left shadow-[0_18px_50px_-40px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-colors hover:border-white/[0.16] hover:bg-white/[0.06]"
              >
                <span className="grid size-11 place-items-center rounded-xl border border-white/[0.12] bg-gradient-to-br from-[#57e4a5]/40 to-[#3bd6d0]/30">
                  <UploadIcon className="size-5 text-white" />
                </span>
                <span className="mt-1 text-base font-semibold text-white">Upload a Book</span>
                <span className="text-sm text-white/55">
                  Drop in your own PDF and choose the subject and grade manually.
                </span>
              </motion.button>
            </motion.div>
          </>
        )}

        {source === "upload" && (
          <>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Upload a curriculum book
            </h1>
            <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-white/55">
              The system extracts the table of contents and assembles a homework packet
              aligned to the source material.
            </p>

            <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <label htmlFor="subject" className={LBL}>
                  Subject
                </label>
                <Select
                  value={subject}
                  onValueChange={(v) => setSubject(v as Subject)}
                  disabled={busy}
                >
                  <SelectTrigger id="subject" className={SELECT_TRIGGER}>
                    <SelectValue placeholder="Choose a subject" />
                  </SelectTrigger>
                  <SelectContent>
                    {SUBJECTS.map((s) => (
                      <SelectItem key={s} value={s}>
                        {subjectLabel(s)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="grade" className={LBL}>
                  Grade (optional)
                </label>
                <Select value={grade} onValueChange={setGrade} disabled={busy}>
                  <SelectTrigger id="grade" className={SELECT_TRIGGER}>
                    <SelectValue placeholder="Choose a grade" />
                  </SelectTrigger>
                  <SelectContent>
                    {GRADES.map((g) => (
                      <SelectItem key={g} value={g}>
                        {g}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-xs text-white/45">
                  Files the homework into the matching Notion lesson page.
                </span>
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="source-language" className={LBL}>
                  Book language
                </label>
                <Select
                  value={sourceLanguage}
                  onValueChange={(v) => setSourceLanguage(v as OutputLanguage)}
                  disabled={busy}
                >
                  <SelectTrigger id="source-language" className={SELECT_TRIGGER}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="uz">UZ — O'zbek</SelectItem>
                    <SelectItem value="ru">RU — Русский</SelectItem>
                    <SelectItem value="en">EN — English</SelectItem>
                  </SelectContent>
                </Select>
                <span className="text-xs text-white/45">
                  The language the textbook is written in. Defaults to Uzbek.
                </span>
              </div>

              <div className="flex flex-col gap-2">
                <span className={LBL}>PDF</span>
                <div
                  {...dz.getRootProps()}
                  className={cn(
                    "cursor-pointer rounded-2xl border border-dashed px-4 py-8 text-center backdrop-blur-xl transition-colors",
                    dz.isDragActive
                      ? "border-[#5b8dff]/70 bg-[#5b8dff]/[0.08]"
                      : "border-white/[0.18] bg-white/[0.03] hover:border-white/[0.3] hover:bg-white/[0.06]",
                    busy && "pointer-events-none opacity-60",
                  )}
                >
                  <input {...dz.getInputProps()} />
                  {file ? (
                    <div className="flex items-center justify-center gap-2.5">
                      <FileText className="size-4 text-[#9cc0ff]" />
                      <span className="text-sm font-medium text-white">{file.name}</span>
                      <span className="font-mono text-[0.7rem] text-white/45">
                        · {(file.size / 1024 / 1024).toFixed(1)}MB · click to replace
                      </span>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-1.5">
                      <UploadIcon className="size-5 text-white/40" />
                      <span className="text-sm text-white/80">
                        {dz.isDragActive ? "Drop the file" : "Drop a PDF, or click to browse"}
                      </span>
                      <span className="font-mono text-[0.66rem] text-white/40">Up to 50 MB</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-3">
                <button type="submit" disabled={busy} className={PRIMARY_BTN}>
                  {busy ? (
                    <>
                      <Loader2 className="size-4 animate-spin" />
                      Uploading…
                    </>
                  ) : (
                    <>
                      Upload
                      <ArrowRight className="size-4" />
                    </>
                  )}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setSource("choose")}
                  className={GHOST_BTN}
                >
                  <ArrowLeft className="size-4" />
                  Back
                </button>
              </div>
            </form>
          </>
        )}

        {source === "notion" && (
          <>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Fetch from Notion
            </h1>
            <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-white/55">
              Pick a grade, then a subject. The textbook is pulled from the Notion library and a new
              session is created for it.
            </p>

            <div className="mt-8 flex flex-col gap-5">
              {nErr ? (
                <div className="flex flex-col items-start gap-3 rounded-2xl border border-white/[0.09] bg-white/[0.04] px-4 py-5 backdrop-blur-xl">
                  <span className="text-sm text-white/60">{nErr}</span>
                  <button type="button" onClick={() => setSource("upload")} className={GLASS_BTN}>
                    Use upload instead
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex flex-col gap-2">
                    <label htmlFor="notion-grade" className={LBL}>
                      Grade
                    </label>
                    {grades === null ? (
                      <div className="flex items-center gap-2 text-sm text-white/50">
                        <Loader2 className="size-4 animate-spin" />
                        Loading grades…
                      </div>
                    ) : (
                      <Select
                        onValueChange={(pageId) => {
                          const g = grades.find((x) => x.page_id === pageId);
                          if (g) void pickGrade(g.page_id, g.title);
                        }}
                        disabled={busy}
                      >
                        <SelectTrigger id="notion-grade" className={SELECT_TRIGGER}>
                          <SelectValue placeholder="Choose a grade" />
                        </SelectTrigger>
                        <SelectContent>
                          {grades.map((g) => (
                            <SelectItem key={g.page_id} value={g.page_id}>
                              {g.title}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </div>

                  {nGrade !== "" && (
                    <div className="flex flex-col gap-2">
                      <span className={LBL}>Subject</span>
                      {subjects === null ? (
                        <div className="flex items-center gap-2 text-sm text-white/50">
                          <Loader2 className="size-4 animate-spin" />
                          Loading subjects…
                        </div>
                      ) : subjects.length === 0 ? (
                        <span className="text-sm text-white/50">
                          No subjects found for this grade.
                        </span>
                      ) : (
                        <div className="flex flex-col gap-3">
                          {subjects.map((s) => {
                            const usable = !!s.app_subject && s.has_textbook;
                            const reason = !s.has_textbook
                              ? "no textbook"
                              : !s.app_subject
                                ? "unsupported"
                                : null;
                            // Language availability for this subject (null = map not yet loaded).
                            const langMap = s.app_subject ? (availLangs?.[s.app_subject] ?? null) : null;
                            // The ambiguous-file picker's current selection (if any) for THIS
                            // subject — its own status governs the "Fetch selected" gate
                            // (PR #99 gate finding 3), not the part rollup.
                            const candStatus =
                              candidatePick?.subject.page_id === s.page_id && candidatePick.selected
                                ? candidatePrepareStatus(candidatePick.selected)
                                : null;
                            const candBlockedTooltip = candStatus ? proceedBlockedTooltip(candStatus) : undefined;
                            return (
                              <div
                                key={s.page_id}
                                className={cn(
                                  "rounded-2xl border border-white/[0.09] bg-white/[0.04] px-4 py-3 backdrop-blur-xl",
                                  !usable && "opacity-50",
                                  busy && "pointer-events-none opacity-60",
                                )}
                              >
                                <div className="flex items-center justify-between gap-3">
                                  <span className={cn("text-sm font-medium", usable ? "text-white" : "text-white/60")}>
                                    {s.notion_title}
                                  </span>
                                  {reason ? (
                                    <span className="font-mono text-[0.66rem] text-white/45">
                                      {reason}
                                    </span>
                                  ) : s.page_id === pendingSubjectId ? (
                                    <Loader2 className="size-4 animate-spin text-white/45" />
                                  ) : null}
                                </div>
                                {/* Language chips — only for usable subjects */}
                                {usable && (
                                  <div className="mt-2 flex flex-wrap gap-2">
                                    {(["uz", "ru", "en"] as OutputLanguage[]).map((lang) => {
                                      const mapLoaded = availLangs != null;
                                      const { available, multiPart, partCount } = langChipState(lang, langMap, mapLoaded);
                                      // System-aware gate (PR #99 gate finding 2): the language
                                      // chip IS the primary prepare action here (no separate
                                      // "Prepare" button like launcher.tsx), so it must respect
                                      // the resolved part's own status the same way. An
                                      // as-yet-unresolved two-linked part still falls back to
                                      // `textbook_ready`/proceed:true, so this never blocks
                                      // entering the file picker — only a resolved single part
                                      // that's already prepared/preparing/needs-review/failed.
                                      const part = partForResolution(s.page_id, lang, langMap);
                                      const partStatus = partPrepareStatus(part);
                                      const blockedTooltip = proceedBlockedTooltip(partStatus);
                                      const tooltip = multiPart
                                        ? `${partCount} ${LANG_LABEL[lang]} textbook parts in Notion — pick the specific part from that language's subject list, or upload the PDF directly.`
                                        : !available && lang === "en"
                                          ? "No English page yet — create an English page (with the textbook) in Notion, or upload the PDF directly."
                                          : !available
                                            ? `No ${LANG_LABEL[lang]} textbook available in Notion for this subject.`
                                            : blockedTooltip;
                                      return (
                                        <button
                                          key={lang}
                                          type="button"
                                          title={tooltip}
                                          disabled={!available || busy || !partStatus.actions.proceed}
                                          onClick={() => {
                                            // Defense-in-depth — see the disabled= condition above.
                                            if (!partStatus.actions.proceed) return;
                                            void pickSubject(s, lang);
                                          }}
                                          className={cn(
                                            "flex items-center gap-1.5 rounded-xl border px-2.5 py-1 text-xs font-medium transition-all",
                                            available && partStatus.actions.proceed
                                              ? "border-white/[0.14] bg-white/[0.05] text-white/75 hover:border-white/[0.25] hover:bg-white/[0.1] hover:text-white"
                                              : "cursor-not-allowed border-white/[0.06] bg-transparent text-white/25 opacity-50",
                                          )}
                                        >
                                          {lang.toUpperCase()}
                                          {!available && <span className="text-[0.6rem]">✕</span>}
                                        </button>
                                      );
                                    })}
                                  </div>
                                )}
                                {/* System-aware chips (task 5): a language above may resolve
                                    to a part already PREPARED/PREPARING/NEEDS REVIEW/FAILED —
                                    renders nothing for an unprepared (textbook-ready) part, so
                                    the button row above is unchanged for that (common) case.
                                    When the ambiguous-file picker below has an explicit
                                    selection for THIS subject+language, that candidate's own
                                    status governs instead of the part rollup (PR #99 gate
                                    finding 3 — resolvedPrepareStatus). */}
                                {usable && (
                                  <div className="mt-2 flex flex-col gap-2">
                                    {(["uz", "ru", "en"] as OutputLanguage[]).map((lang) => {
                                      const part = partForResolution(s.page_id, lang, langMap);
                                      const selectedForLang =
                                        candidatePick?.subject.page_id === s.page_id && candidatePick.language === lang
                                          ? candidatePick.selected
                                          : null;
                                      const status = resolvedPrepareStatus(part, selectedForLang);
                                      if (
                                        status.panel.kind === "no_textbook" ||
                                        status.panel.kind === "textbook_ready"
                                      ) {
                                        return null;
                                      }
                                      return (
                                        <div key={lang} className="flex items-start gap-2">
                                          <span className="mt-0.5 font-mono text-[0.6rem] uppercase tracking-wide text-white/40">
                                            {lang}
                                          </span>
                                          <PrepareStatusPanel status={status} />
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                                {/* File-level candidate picker — shown only when the resolved
                                    part carries >1 file in its best rank tier. Prepare stays
                                    disabled with a hint until a candidate is picked. */}
                                {usable && candidatePick && candidatePick.subject.page_id === s.page_id && (
                                  <div className="mt-3 flex flex-col gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] p-3">
                                    <span className="text-xs text-white/60">
                                      Multiple {LANG_LABEL[candidatePick.language]} files found for this subject
                                      — pick which one to fetch.
                                    </span>
                                    <Select
                                      value={candidatePick.selected?.block_id ?? ""}
                                      onValueChange={(blockId) => {
                                        const selected =
                                          candidatePick.candidates.find((c) => c.block_id === blockId) ?? null;
                                        setCandidatePick({ ...candidatePick, selected });
                                      }}
                                    >
                                      <SelectTrigger className={SELECT_TRIGGER}>
                                        <SelectValue placeholder="Choose a file" />
                                      </SelectTrigger>
                                      <SelectContent>
                                        {candidatePick.candidates.map((c) => (
                                          <SelectItem key={c.block_id} value={c.block_id}>
                                            {c.filename}
                                          </SelectItem>
                                        ))}
                                      </SelectContent>
                                    </Select>
                                    {/* The selected candidate's own system state (PR #99 gate
                                        finding 3) — renders nothing when it's the common
                                        unprepared (textbook-ready) case. */}
                                    {candStatus &&
                                      candStatus.panel.kind !== "no_textbook" &&
                                      candStatus.panel.kind !== "textbook_ready" && (
                                        <PrepareStatusPanel status={candStatus} />
                                      )}
                                    <div className="flex items-center gap-2">
                                      <button
                                        type="button"
                                        disabled={!candidatePick.selected || busy || (candStatus ? !candStatus.actions.proceed : false)}
                                        title={
                                          !candidatePick.selected
                                            ? "Pick a file to continue"
                                            : candBlockedTooltip
                                        }
                                        onClick={() => {
                                          if (!candidatePick.selected) return;
                                          // Defense-in-depth — see the disabled= condition above.
                                          if (candStatus && !candStatus.actions.proceed) return;
                                          void runFetch(
                                            candidatePick.subject,
                                            candidatePick.language,
                                            candidatePick.partPageId,
                                            candidatePick.selected.block_id,
                                          );
                                        }}
                                        className={cn(
                                          GLASS_BTN,
                                          (!candidatePick.selected || (candStatus && !candStatus.actions.proceed)) &&
                                            "cursor-not-allowed opacity-50",
                                        )}
                                      >
                                        Fetch selected
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => setCandidatePick(null)}
                                        className={GHOST_BTN}
                                      >
                                        Cancel
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}

              <button
                type="button"
                disabled={busy}
                onClick={() => setSource("choose")}
                className={cn(GHOST_BTN, "self-start")}
              >
                <ArrowLeft className="size-4" />
                Back
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
