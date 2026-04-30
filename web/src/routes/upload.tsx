import { ArrowRight, FileText, Loader2, Upload as UploadIcon } from "lucide-react";
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
import { SUBJECTS, type Subject } from "@/lib/types";
import { cn } from "@/lib/utils";

export function UploadPage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState<Subject | "">("");
  const [busy, setBusy] = useState(false);

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
      const book = await api.uploadBook(file, subject as Subject);
      toast.success("Uploaded.");
      navigate(`/book/${book.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      toast.error(msg);
      setBusy(false);
    }
  }

  return (
    <>
      <Eyebrow>New session</Eyebrow>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
        Upload a curriculum book
      </h1>
      <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
        The system extracts the table of contents, classifies the lesson you choose, and assembles a
        homework packet aligned to the source material.
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
      </form>
    </>
  );
}
