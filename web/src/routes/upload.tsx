import {
  ArrowLeft,
  ArrowRight,
  FileText,
  Library,
  Loader2,
  Upload as UploadIcon,
} from "lucide-react";
import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Eyebrow } from "@/components/eyebrow";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import {
  type NotionGrade,
  type NotionSubject,
  SUBJECTS,
  type Subject,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const GRADES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"];

export function UploadPage() {
  const navigate = useNavigate();
  const [source, setSource] = useState<"choose" | "upload" | "notion">("choose");
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState<Subject | "">("");
  const [grade, setGrade] = useState("");
  const [busy, setBusy] = useState(false);

  const [nGrade, setNGrade] = useState("");
  const [grades, setGrades] = useState<NotionGrade[] | null>(null);
  const [subjects, setSubjects] = useState<NotionSubject[] | null>(null);
  const [nErr, setNErr] = useState<string | null>(null);

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
      const book = await api.uploadBook(file, subject as Subject, grade || undefined);
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
    setNErr(null);
    try {
      setSubjects(await api.listNotionSubjects(gradePageId));
    } catch (e) {
      setNErr(e instanceof Error ? e.message : "Could not load subjects");
    }
  }

  async function pickSubject(s: NotionSubject) {
    if (!s.app_subject || !s.has_textbook) return;
    setBusy(true);
    try {
      const book = await api.fetchBookFromNotion(s.page_id, nGrade);
      toast.success("Fetched.");
      navigate(`/book/${book.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Fetch failed");
      setBusy(false);
    }
  }

  return (
    <>
      <Eyebrow>New session</Eyebrow>

      {source === "choose" && (
        <>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
            Start a new session
          </h1>
          <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
            Fetch a curriculum book straight from Notion, or upload your own PDF. Either way the
            system extracts the table of contents and assembles a homework packet aligned to the
            source material.
          </p>

          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            <button
              type="button"
              onClick={enterNotion}
              className={cn(
                "flex flex-col items-start gap-2 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-5 py-6 text-left transition-colors",
                "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
              )}
            >
              <Library className="size-6 text-(--color-accent)" />
              <span className="text-base font-medium text-(--color-ink)">Fetch From Notion</span>
              <span className="text-sm text-(--color-ink-soft)">
                Pick a grade and subject; the textbook is pulled from the Notion library.
              </span>
            </button>

            <button
              type="button"
              onClick={() => setSource("upload")}
              className={cn(
                "flex flex-col items-start gap-2 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-5 py-6 text-left transition-colors",
                "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
              )}
            >
              <UploadIcon className="size-6 text-(--color-accent)" />
              <span className="text-base font-medium text-(--color-ink)">Upload a Book</span>
              <span className="text-sm text-(--color-ink-soft)">
                Drop in your own PDF and choose the subject and grade manually.
              </span>
            </button>
          </div>
        </>
      )}

      {source === "upload" && (
        <>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
            Upload a curriculum book
          </h1>
          <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
            The system extracts the table of contents, classifies the lesson you choose, and
            assembles a homework packet aligned to the source material.
          </p>

          <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor="subject">Subject</Label>
              <Select
                value={subject}
                onValueChange={(v) => setSubject(v as Subject)}
                disabled={busy}
              >
                <SelectTrigger id="subject">
                  <SelectValue placeholder="Choose a subject" />
                </SelectTrigger>
                <SelectContent>
                  {SUBJECTS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="grade">Grade (optional)</Label>
              <Select value={grade} onValueChange={setGrade} disabled={busy}>
                <SelectTrigger id="grade">
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
              <span className="text-xs text-(--color-ink-muted)">
                Files the homework into the matching Notion lesson page.
              </span>
            </div>

            <div className="flex flex-col gap-2">
              <Label>PDF</Label>
              <div
                {...dz.getRootProps()}
                className={cn(
                  "cursor-pointer rounded-(--radius-md) border border-dashed bg-(--color-elevated) px-4 py-7 text-center transition-colors",
                  "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
                  dz.isDragActive
                    ? "border-(--color-accent) bg-(--color-accent-soft)"
                    : "border-(--color-border)",
                  busy && "pointer-events-none opacity-60",
                )}
              >
                <input {...dz.getInputProps()} />
                {file ? (
                  <div className="flex items-center justify-center gap-2.5">
                    <FileText className="size-4 text-(--color-accent)" />
                    <span className="text-sm font-medium text-(--color-ink)">{file.name}</span>
                    <span className="font-mono text-[0.7rem] text-(--color-ink-muted)">
                      · {(file.size / 1024 / 1024).toFixed(1)}MB · click to replace
                    </span>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-1.5">
                    <UploadIcon className="size-5 text-(--color-ink-muted)" />
                    <span className="text-sm text-(--color-ink)">
                      {dz.isDragActive ? "Drop the file" : "Drop a PDF, or click to browse"}
                    </span>
                    <span className="font-mono text-[0.66rem] text-(--color-ink-muted)">
                      Up to 50 MB
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Button type="submit" disabled={busy} className="self-start">
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
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => setSource("choose")}
              >
                <ArrowLeft className="size-4" />
                Back
              </Button>
            </div>
          </form>
        </>
      )}

      {source === "notion" && (
        <>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
            Fetch from Notion
          </h1>
          <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
            Pick a grade, then a subject. The textbook is pulled from the Notion library and a new
            session is created for it.
          </p>

          <div className="mt-8 flex flex-col gap-5">
            {nErr ? (
              <div className="flex flex-col items-start gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-4 py-5">
                <span className="text-sm text-(--color-ink-soft)">{nErr}</span>
                <Button type="button" variant="secondary" onClick={() => setSource("upload")}>
                  Use upload instead
                </Button>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="notion-grade">Grade</Label>
                  {grades === null ? (
                    <div className="flex items-center gap-2 text-sm text-(--color-ink-muted)">
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
                      <SelectTrigger id="notion-grade">
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
                    <Label>Subject</Label>
                    {subjects === null ? (
                      <div className="flex items-center gap-2 text-sm text-(--color-ink-muted)">
                        <Loader2 className="size-4 animate-spin" />
                        Loading subjects…
                      </div>
                    ) : subjects.length === 0 ? (
                      <span className="text-sm text-(--color-ink-muted)">
                        No subjects found for this grade.
                      </span>
                    ) : (
                      <div className="flex flex-col gap-2">
                        {subjects.map((s) => {
                          const usable = !!s.app_subject && s.has_textbook;
                          const reason = !s.has_textbook
                            ? "no textbook"
                            : !s.app_subject
                              ? "unsupported"
                              : null;
                          return (
                            <button
                              key={s.page_id}
                              type="button"
                              disabled={!usable || busy}
                              onClick={() => void pickSubject(s)}
                              className={cn(
                                "flex items-center justify-between gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-4 py-3 text-left transition-colors",
                                usable
                                  ? "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)"
                                  : "cursor-not-allowed opacity-60",
                                busy && "pointer-events-none opacity-60",
                              )}
                            >
                              <span className="text-sm font-medium text-(--color-ink)">
                                {s.notion_title}
                              </span>
                              {reason ? (
                                <span className="font-mono text-[0.66rem] text-(--color-ink-muted)">
                                  {reason}
                                </span>
                              ) : busy ? (
                                <Loader2 className="size-4 animate-spin text-(--color-ink-muted)" />
                              ) : (
                                <ArrowRight className="size-4 text-(--color-ink-muted)" />
                              )}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => setSource("choose")}
              className="self-start"
            >
              <ArrowLeft className="size-4" />
              Back
            </Button>
          </div>
        </>
      )}
    </>
  );
}
