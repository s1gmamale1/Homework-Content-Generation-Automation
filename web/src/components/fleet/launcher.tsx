import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Rocket } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { subjectLabel } from "@/lib/subjects";
import type { BatchSummary, Book } from "@/lib/types";
import { CARD, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";

const LBL = "text-xs font-medium uppercase tracking-[0.12em] text-white/45";

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
  const batchedBookIds = new Set((batches ?? []).map((b) => b.book_id));
  const all = books ?? [];
  const preparing = all.filter(
    (b) => b.status === "toc_extracting" || b.status === "uploading",
  );
  const failed = all.filter((b) => b.status === "failed");
  const ready = all.filter(
    (b) => b.status === "toc_ready" && !batchedBookIds.has(b.id),
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
                  <ReadyRow key={b.id} book={b} />
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

function ReadyRow({ book }: { book: Book }) {
  const qc = useQueryClient();
  const [provider, setProvider] = useState("claude");

  const modelsQ = useQuery({
    queryKey: ["agent-models"],
    queryFn: api.getAgentModels,
  });
  const detail = useQuery({
    queryKey: ["book", book.id],
    queryFn: () => api.getBook(book.id),
  });
  const lessons = detail.data?.toc?.length;

  const launch = useMutation({
    mutationFn: () => api.launchBatch({ book_id: book.id, provider }),
    onSuccess: (r) => {
      toast.success(`Launched ${r.jobs_created} lessons`);
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Launch failed"),
  });

  const providers = Object.keys(modelsQ.data?.providers ?? {});

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5">
      <div className="min-w-0">
        <div className="text-sm font-medium text-white">
          {subjectLabel(book.subject)}
        </div>
        <div className="text-xs text-white/45">{lessons ?? "…"} lessons</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
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
        <button
          type="button"
          className={PRIMARY_BTN}
          disabled={launch.isPending}
          onClick={() => launch.mutate()}
        >
          {launch.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Rocket className="size-4" />
          )}
          Launch
        </button>
      </div>
    </div>
  );
}
