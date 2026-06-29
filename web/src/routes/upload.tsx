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
import { subjectLabel } from "@/lib/subjects";
import {
  type NotionGrade,
  type NotionSubject,
  type OutputLanguage,
  SUBJECTS,
  type Subject,
} from "@/lib/types";
import { GHOST_BTN, GLASS_BTN, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";

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
  const [grades, setGrades] = useState<NotionGrade[] | null>(null);
  const [subjects, setSubjects] = useState<NotionSubject[] | null>(null);
  const [pendingSubjectId, setPendingSubjectId] = useState<string | null>(null);
  const [nErr, setNErr] = useState<string | null>(null);
  // Available language containers per app_subject for the picked Notion grade.
  const [availLangs, setAvailLangs] = useState<Record<string, Record<string, { page_id: string; has_textbook: boolean }>> | null>(null);

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
      toast.success("Uploaded.");
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
    setSubjects(null);
    setPendingSubjectId(null);
    setAvailLangs(null);
    setNErr(null);
    try {
      // Fetch subjects and available-languages in parallel.
      const [subs, langs] = await Promise.all([
        api.listNotionSubjects(gradePageId),
        api.fetchAvailableLanguages(gradePageId).catch(() => null),
      ]);
      setSubjects(subs);
      setAvailLangs(langs);
    } catch (e) {
      setNErr(e instanceof Error ? e.message : "Could not load subjects");
    }
  }

  async function pickSubject(s: NotionSubject, language: OutputLanguage) {
    if (!s.app_subject || !s.has_textbook) return;
    if (busy) return; // guard against a second fetch while one is in flight
    setPendingSubjectId(s.page_id);
    setBusy(true);
    try {
      const book = await api.fetchBookFromNotion(
        s.page_id,
        nGrade,
        language !== "uz" ? language : undefined,
      );
      toast.success("Fetched.");
      navigate(`/book/${book.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Fetch failed");
      setBusy(false);
      setPendingSubjectId(null);
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
                                      const info = langMap?.[lang];
                                      const mapLoaded = availLangs != null;
                                      const available = !mapLoaded || (info != null && info.has_textbook);
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
                                          disabled={!available || busy}
                                          onClick={() => void pickSubject(s, lang)}
                                          className={cn(
                                            "flex items-center gap-1.5 rounded-xl border px-2.5 py-1 text-xs font-medium transition-all",
                                            available
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
