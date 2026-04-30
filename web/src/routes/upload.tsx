import { motion } from "motion/react";
import { ArrowUpRight, FileText, Loader2, Upload as UploadIcon } from "lucide-react";
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

const fadeUp = {
  hidden: { opacity: 0, y: 12, filter: "blur(6px)" },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { delay: 0.05 + i * 0.08, duration: 0.7, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

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
      toast.success("Uploaded — extracting table of contents.");
      navigate(`/book/${book.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      toast.error(msg);
      setBusy(false);
    }
  }

  return (
    <>
      <motion.div initial="hidden" animate="show" variants={fadeUp} custom={0}>
        <Eyebrow>Begin</Eyebrow>
      </motion.div>

      <motion.h1
        initial="hidden"
        animate="show"
        variants={fadeUp}
        custom={1}
        className="mt-6 font-display text-[clamp(2.8rem,6.8vw,4.6rem)] font-normal leading-[1.02] tracking-[-0.025em] text-(--color-ink)"
      >
        Compose <em className="not-italic gradient-text font-display italic">homework</em>,
        faithfully drawn from a curriculum book.
      </motion.h1>

      <motion.p
        initial="hidden"
        animate="show"
        variants={fadeUp}
        custom={2}
        className="mt-5 max-w-[58ch] text-[1.1rem] leading-[1.65] text-(--color-ink-soft)"
      >
        Upload a textbook PDF; the system indexes its table of contents, classifies the
        difficulty of the lesson you choose, and assembles a section-by-section study packet
        aligned to the source material.
      </motion.p>

      <motion.form
        initial="hidden"
        animate="show"
        variants={fadeUp}
        custom={3}
        onSubmit={handleSubmit}
        className="mt-12 flex flex-col gap-6"
      >
        <div className="flex flex-col gap-2.5">
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

        <div className="flex flex-col gap-2.5">
          <Label>Manuscript · PDF</Label>
          <div
            {...dz.getRootProps()}
            className={cn(
              "group relative cursor-pointer rounded-(--radius-lg) border border-dashed bg-(--color-surface) backdrop-blur-md transition-all duration-300 ease-(--ease-soft)",
              "hover:bg-(--color-surface-hover) hover:border-(--color-amber)/60",
              dz.isDragActive
                ? "border-(--color-amber) bg-[oklch(0.79_0.13_70_/_8%)] scale-[1.01]"
                : "border-(--color-border-hover)",
              busy && "pointer-events-none opacity-60",
            )}
          >
            <input {...dz.getInputProps()} />
            <div className="flex flex-col items-center justify-center gap-3 px-6 py-10 text-center">
              {file ? (
                <>
                  <FileText className="size-7 text-(--color-amber)" />
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-medium text-(--color-ink)">{file.name}</span>
                    <span className="font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                      {(file.size / 1024 / 1024).toFixed(2)} MB · click to replace
                    </span>
                  </div>
                </>
              ) : (
                <>
                  <UploadIcon className="size-7 text-(--color-ink-muted) transition-colors group-hover:text-(--color-amber)" />
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-medium text-(--color-ink)">
                      {dz.isDragActive ? "Drop the manuscript" : "Drop a PDF, or click to browse"}
                    </span>
                    <span className="font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                      Up to 50 MB
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        <Button type="submit" size="lg" disabled={busy} className="mt-2 self-start">
          {busy ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Uploading manuscript…
            </>
          ) : (
            <>
              Upload &amp; Compose
              <ArrowUpRight className="size-4" />
            </>
          )}
        </Button>
      </motion.form>
    </>
  );
}
