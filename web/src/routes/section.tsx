import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Eyebrow } from "@/components/eyebrow";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatPages } from "@/lib/utils";

export function SectionPage() {
  const { bookId, sectionId } = useParams<{ bookId: string; sectionId: string }>();
  const navigate = useNavigate();
  const [generating, setGenerating] = useState(false);

  const { data: book, isLoading } = useQuery({
    queryKey: ["book", bookId],
    queryFn: () => (bookId ? api.getBook(bookId) : Promise.reject(new Error("no id"))),
    enabled: Boolean(bookId),
  });

  const section = book?.toc?.find((e) => e.id === sectionId);

  async function handleGenerate() {
    if (!bookId || !sectionId) return;
    setGenerating(true);
    try {
      const job = await api.generate(bookId, sectionId);
      navigate(`/job/${job.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Generate failed";
      toast.error(msg);
      setGenerating(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-(--color-ink-muted)">
        <Loader2 className="size-3.5 animate-spin" />
        Loading section…
      </div>
    );
  }

  if (!book || !section) {
    return (
      <>
        <Eyebrow>Section</Eyebrow>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">Section not found</h1>
        <p className="mt-2 text-sm text-(--color-ink-soft)">
          This section may have been removed, or the URL is malformed.
        </p>
        <Button asChild variant="secondary" className="mt-6">
          <Link to="/library">
            <ArrowLeft className="size-3.5" />
            Back to library
          </Link>
        </Button>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Link
          to={`/book/${book.id}`}
          className="inline-flex items-center gap-1.5 font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:text-(--color-ink)"
        >
          <ArrowLeft className="size-3.5" />
          {book.original_filename}
        </Link>
        <Badge variant="neutral">{book.subject}</Badge>
      </div>

      {section.chapter_title && (
        <p className="mt-6 font-mono text-[0.7rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
          {section.chapter_title}
        </p>
      )}

      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
        {section.section_number ? `${section.section_number} · ` : ""}
        {section.section_title}
      </h1>

      {section.page_start && (
        <p className="mt-2 font-mono text-sm text-(--color-ink-muted)">
          {formatPages(section.page_start, section.page_end)}
        </p>
      )}

      <div className="mt-8 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-5">
        <h2 className="text-sm font-semibold tracking-tight text-(--color-ink)">
          Generate homework
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-(--color-ink-soft)">
          Run the curriculum pipeline against this section. It will read the lesson, classify
          difficulty, and produce the assembled study packet.
        </p>
        <Button onClick={handleGenerate} disabled={generating} className="mt-4">
          {generating ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Sending to compositor…
            </>
          ) : (
            <>
              <Sparkles className="size-4" />
              Generate homework
              <ArrowRight className="size-4" />
            </>
          )}
        </Button>
      </div>
    </>
  );
}
