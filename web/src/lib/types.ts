/* Mirrors the Pydantic schemas in app/schemas/. Keep in sync if the API changes. */

/**
 * All curriculum subjects. SOURCE OF TRUTH: app/services/subjects.py
 * (keep this list in sync with that registry). The first 7 are legacy codes.
 */
export type Subject =
  | "biology"
  | "english"
  | "geometriya-g7-11"
  | "history"
  | "kimyo-g7-11"
  | "math-algebra"
  | "physics"
  | "matematika"
  | "ona-tili"
  | "adabiyot"
  | "russian"
  | "oqish-savodxonligi"
  | "alifbe"
  | "tabiiy-fanlar"
  | "astronomiya"
  | "geografiya"
  | "informatika"
  | "atrof-muhit"
  | "huquq"
  | "iqtisodiyot"
  | "chizmachilik"
  | "musiqa"
  | "tasviriy-sanat"
  | "texnologiya"
  | "tarbiya"
  | "chqbt";

export const SUBJECTS: Subject[] = [
  "biology",
  "english",
  "geometriya-g7-11",
  "history",
  "kimyo-g7-11",
  "math-algebra",
  "physics",
  "matematika",
  "ona-tili",
  "adabiyot",
  "russian",
  "oqish-savodxonligi",
  "alifbe",
  "tabiiy-fanlar",
  "astronomiya",
  "geografiya",
  "informatika",
  "atrof-muhit",
  "huquq",
  "iqtisodiyot",
  "chizmachilik",
  "musiqa",
  "tasviriy-sanat",
  "texnologiya",
  "tarbiya",
  "chqbt",
];

export type BookStatus = "uploading" | "toc_extracting" | "toc_ready" | "toc_review" | "failed";

/** Job/batch kind discriminator (Task 8). "homework" (default) is the
 *  full multi-phase flow; "teacher_material" is the fixed single-phase
 *  teacher-deck flow — its own batch, never adopts/resumes a homework job. */
export type JobKind = "homework" | "teacher_material";

/** Generation transport: "cli" (local subprocess) vs "api" (pay-per-token SDK). */
export type Transport = "cli" | "api";

/** Output language for generated content. */
export type OutputLanguage = "uz" | "en" | "ru";

/** Per-role billing override for the extract/judge phases: "inherit" (default)
 *  follows the job's `transport`; "cli"/"api" pin that role explicitly. */
export type RoleTransport = "cli" | "api" | "inherit";

/** Per-batch strategy when a Claude session-limit hit is detected.
 *  "pause" = pause the batch and wait; "switch" = failover to next provider;
 *  "inherit" = follow settings.session_limit_strategy (fleet default). */
export type SessionLimitStrategy = "pause" | "switch" | "inherit";

export interface TOCEntry {
  id: string;
  chapter_number: string | null;
  chapter_title: string | null;
  section_number: string;
  section_title: string;
  page_start: number | null;
  page_end: number | null;
  order_index: number;
  latest_job_id?: string | null;
  latest_job_status?: JobStatus | null;
  /** Server-computed TOC row classification: "lesson" | "header" | "recall" |
   *  "practice" | "revision" | "test" | "other". The FE displays this — never
   *  re-derives it. */
  entry_class: string | null;
}

export interface Book {
  id: string;
  subject: Subject;
  grade: string | null;
  original_filename: string;
  subject_variant?: string | null;
  source_language: OutputLanguage;
  status: BookStatus;
  error_message: string | null;
  toc_validation?: "verified" | "mismatch" | "skipped" | null;
  toc_validation_detail?: string | null;
  gemini_file_expires_at: string | null;
  file_size_bytes: number | null;
  created_at: string | null;
  toc: TOCEntry[] | null;
  /** True when the upload/fetch reused an existing book (sha dedup) — no
   *  extraction runs, so the UI must not show a "Preparing" state. */
  deduplicated?: boolean;
  /** Script-guard advisories / scanned-PDF skip notices from a from-notion
   *  prepare call; null/absent when there's nothing to warn about. */
  warnings?: string[] | null;
}

export interface NotionGrade {
  title: string;
  page_id: string;
}

export interface NotionSubject {
  notion_title: string;
  page_id: string;
  app_subject: string | null;
  has_textbook: boolean;
}

/** System-state fields the backend enriches onto a candidate — and, when
 *  exactly one candidate resolves unambiguously, onto its owning part too —
 *  whenever `(page_id, block_id)` is already linked to a book row
 *  (`book_notion_sources`, worklog 0144 task 4). Absent/null when unlinked.
 *  Drives the "Prepare a subject" dialog's system-aware chips (task 5,
 *  `lib/prepare-status.ts`). */
export interface BookLinkState {
  book_id?: string | null;
  book_status?: BookStatus | null;
  toc_validation?: "verified" | "mismatch" | "skipped" | null;
  /** Whole-book TOC row count as of the last extraction. */
  toc_total?: number | null;
  /** ISO timestamp of the toc_ready lifecycle stamp (task 3); null if never
   *  accepted (or accepted before the stamp existed). */
  toc_ready_at?: string | null;
  /** Count of homework jobs that would be orphaned by a TOC redo — mirrors
   *  the `toc_retry_blocked_by_jobs` 409's `count`. 0 = redo is safe. */
  redo_blocked_by_jobs?: number | null;
}

/** One candidate PDF file found for a part: `rank` 0=textbook, 1=neutral,
 *  2=workbook — lower is more authoritative. `page_id` may be a CHILD page's
 *  id distinct from the owning part's `page_id` (nested/child-page parts);
 *  callers must fetch using the CANDIDATE's page_id, not the part's. */
export interface NotionCandidate extends BookLinkState {
  page_id: string;
  block_id: string;
  filename: string;
  rank: number;
  url?: string;
}

/** One textbook part under a subject/language (multi-volume subjects have >1). */
export interface LangPart extends BookLinkState {
  page_id: string;
  title: string;
  has_textbook: boolean;
  /** File-level candidates for this part (BE-19 task 6); absent on legacy
   *  responses predating the candidate crawl. */
  candidates?: NotionCandidate[];
  /** True when EXACTLY ONE of this part's candidates resolved to a linked
   *  book — the part-level `BookLinkState` fields above are the rollup for
   *  that one book. Absent (or two candidates linked to different books) →
   *  no rollup; only the per-candidate detail is trustworthy. */
  prepared?: boolean;
}

/** Per-language availability for a subject. `page_id`/`has_textbook` are the
 *  first part (backward-compat); `parts` lists every part (may be absent on
 *  legacy responses). */
export interface LangAvailability {
  page_id: string;
  has_textbook: boolean;
  parts?: LangPart[];
}

/** `available-languages` response: app_subject → lang → availability. */
export type AvailableLanguages = Record<string, Record<string, LangAvailability>>;

export type JobStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "cancelling"
  | "cancelled";

/** Persisted answer-key solver outcomes. ``mismatch_blocked`` is terminal:
 * the phase/job are failed and retained only for operator diagnosis. */
export type SolverStatus =
  | "ok"
  | "mismatch_regen"
  | "mismatch_shipped"
  | "mismatch_regen_failed"
  | "mismatch_blocked"
  | "unavailable"
  | "refused";

export interface PhaseOut {
  phase_name: string;
  phase_order: number;
  status: "pending" | "running" | "done" | "failed";
  output_md: string | null;
  tokens_input: number | null;
  tokens_output: number | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  validation_warnings: string[] | null;
  judge_status: string | null;
  solver_status: SolverStatus | null;
}

export interface Job {
  id: string;
  book_id: string;
  toc_entry_id: string;
  subject: Subject;
  status: JobStatus;
  current_phase: string | null;
  error_message: string | null;
  phases: PhaseOut[];
  provider?: string;
  model?: string | null;
  transport: Transport;
  extract_transport: RoleTransport;
  judge_transport: RoleTransport;
  notion_skip_reason?: string | null;
  /** Full content-phase list this job runs (subset closure, or full subject
   *  flow); excludes extract. Lets the UI show queued phases up front. */
  planned_phases?: string[];
}

/** Real-time fleet capability snapshot served by /api/v1/agent/models. */
export interface FleetCapability {
  /** True when at least one worker process is reachable. */
  online: boolean;
  /** Number of worker processes currently connected. */
  workers_online: number;
  /** provider -> CLI available on at least one worker. */
  cli: Record<string, boolean>;
  /** provider -> API credentials present on at least one worker. */
  api: Record<string, boolean>;
}

export interface ProviderModelManifest {
  providers: Record<string, string[]>;
  /** Which providers can run on the pay-per-token API transport. */
  api_supported: Record<string, boolean>;
  /** Providers that have no CLI lane and must stay pinned to API. */
  api_only: Record<string, boolean>;
  /** provider -> model ids that are api-only WITHIN a provider that otherwise
   *  supports cli (e.g. gemini-3.x-flash 404s/ModelNotFoundError on the
   *  gemini CLI's own catalog). Distinct from `api_only` above, which flags a
   *  whole provider (e.g. clodex). Only providers with at least one api-only
   *  model are present; absent/missing entries mean "none". */
  api_only_models?: Record<string, string[]>;
  /** provider -> model -> tier int. */
  tiers?: Record<string, Record<string, number>>;
  /** Live fleet capability snapshot; absent when the endpoint hasn't loaded yet. */
  fleet?: FleetCapability;
}

/* /api/v1/agent/stats — per-provider rolling consumption against caps */
export interface ProviderModelStat {
  model_name: string;
  calls: number;
  duration_secs: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  success_pct: number;
}
/* Per-(provider, transport) rollup. cli rows always cost $0 (no pay-per-token). */
export interface ProviderTransportStat {
  auth_mode: Transport;
  calls: number;
  cost_usd: number;
}
export interface ProviderStatsWindow {
  calls: number;
  duration_secs: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  success_pct: number;
  limit_calls_per_window: number | null;
  pct_of_limit: number | null;
  models: ProviderModelStat[];
  transports: ProviderTransportStat[];
}
export interface UsageSeries {
  calls: number[];
  tokens: number[];
  duration_secs: number[];
  success_pct: number[];
}
export interface AgentStats {
  windows: string[];
  providers: Record<string, Record<string, ProviderStatsWindow>>;
  series: Record<string, UsageSeries>;
  now: string;
}

/* Final Challenge — boss fight */
export interface BossQuestion {
  prompt: string;
  kind: "mc" | "tf" | "ynng" | "open" | string;
  options?: string[];
  correct_index?: number | null;
  correct_answer?: string | null;
  damage?: number;
  bloom_level?: string | null;
  pisa_level?: string | null;
  explanation?: string | null;
  hints?: string[];
}

export interface FinalChallenge {
  title?: string;
  starting_hp: number;
  questions: BossQuestion[];
}

/* Memory Sprint — quick tap quiz */
export interface MemorySprintItem {
  prompt: string;
  kind: "mc" | "tf" | "ynng" | string;
  options?: string[];
  correct_index: number;
  explanation?: string | null;
}

export interface MemorySprintPack {
  items: MemorySprintItem[];
}

/* Reading (English HARD) — passage + checkpoints */
export interface ReadingCheckpoint {
  after_paragraph: number;
  prompt: string;
  options?: string[];
  correct_index?: number | null;
  correct_answer?: string | null;
  explanation?: string | null;
}

export interface ReadingPassage {
  passage_md: string;
  checkpoints: ReadingCheckpoint[];
  cefr_level?: string | null;
}

export type FlashcardType =
  | "definition" | "term_to_meaning" | "formula" | "process_step"
  | "question_answer" | "misconception" | "image_label" | "vocabulary"
  | "grammar" | "example";
export type FlashcardDifficulty = "easy" | "medium" | "hard";

export interface Flashcard {
  id: string;
  front: string;
  back: string;
  type: FlashcardType;
  difficulty: FlashcardDifficulty;
  hint?: string | null;
  explanation?: string | null;
  example?: string | null;
  misconception?: string | null;
  cluster?: string | null;
}

export interface FlashcardsPack {
  cards: Flashcard[];
}

/* Structured games — rendered as interactive React components on the
   /preview/:id route. Mirrors app/schemas/games.py exactly. */

export type GameType = "adaptive_quiz" | "tile_match" | "memory_match" | "sentence_fill";

export interface GameQuestion {
  prompt: string;
  options?: string[];
  correct_index?: number | null;
  answer?: string | null;
  explanation?: string | null;
}

export interface GamePair {
  left: string;
  right: string;
}

export interface GameCard {
  text: string;
  pair_id: number;
}

export interface Game {
  type: GameType | string;
  title: string;
  questions?: GameQuestion[];
  pairs?: GamePair[];
  cards?: GameCard[];
}

export interface GamesPack {
  games: Game[];
}

/* Teacher deck — structured teacher lesson-plan deck for `kind="teacher_material"`
   jobs. Mirrors app/schemas/content_json/teacher_deck.py EXACTLY (field names,
   nesting, optionality). Served via GET /api/v1/jobs/{id}/deck as
   `content_json` on that response. Field KEYS are English; VALUES are
   generated in the book's language. */

export type TeacherDeckBadge = "ekranga" | "teacher_only" | "none";
export type TeacherDeckOptionLabel = "A" | "B" | "C" | "D";

export interface TeacherDeckMeta {
  subject_label: string;
  grade: string;
  topic_number: number;
  topic_title: string;
  duration_min: number;
  lesson_type: string;
  method: string[];
  materials: string[];
  video_ref: string | null;
}

export interface TeacherDeckPassport {
  fan_sinf: string;
  mavzu: string;
  dars_turi: string;
  metod: string;
  kerakli_vosita: string;
  baholash: string;
}

export interface TeacherDeckObjectives {
  bilib_oladi: string;
  qila_oladi: string;
  tushunadi: string;
}

export interface TeacherDeckCoreIdea {
  statement: string;
  elaboration: string;
}

export interface TeacherDeckLessonMapItem {
  index: number;
  title: string;
  description: string;
  minutes: number;
}

export interface TeacherDeckPoint {
  title: string;
  detail: string;
}

export interface TeacherDeckStage {
  index: number;
  title: string;
  minutes: number;
  badge: TeacherDeckBadge;
  points: TeacherDeckPoint[];
  teacher_action: string;
  student_action: string;
  screen_text: string | null;
}

export interface TeacherDeckQuizOption {
  label: TeacherDeckOptionLabel;
  text: string;
}

export interface TeacherDeckQuizItem {
  number: number;
  question: string;
  options: TeacherDeckQuizOption[];
  correct_label: TeacherDeckOptionLabel;
  hint: string;
}

export interface TeacherDeckAnswerKeyItem {
  number: number;
  correct_label: TeacherDeckOptionLabel;
  explanation: string;
}

export interface TeacherDeckPairWorkTask {
  title: string;
  prompt: string;
}

export interface TeacherDeckPairWork {
  intro: string;
  tasks: TeacherDeckPairWorkTask[];
}

export interface TeacherDeckConclusion {
  questions: string[];
}

export interface TeacherDeckRubricComponent {
  points: number;
  title: string;
  detail: string;
}

export interface TeacherDeckRubricBand {
  range: string;
  grade: string;
}

export interface TeacherDeckRubric {
  components: TeacherDeckRubricComponent[];
  total: number;
  bands: TeacherDeckRubricBand[];
}

export interface TeacherDeck {
  meta: TeacherDeckMeta;
  passport: TeacherDeckPassport;
  objectives: TeacherDeckObjectives;
  core_idea: TeacherDeckCoreIdea;
  lesson_map: TeacherDeckLessonMapItem[];
  stages: TeacherDeckStage[];
  quiz: TeacherDeckQuizItem[];
  answer_key: TeacherDeckAnswerKeyItem[];
  pair_work: TeacherDeckPairWork;
  conclusion: TeacherDeckConclusion;
  rubric: TeacherDeckRubric;
}

/** Response from GET /api/v1/jobs/{id}/deck. */
export interface DeckResponse {
  job_id: string;
  content_json: TeacherDeck;
}

/* SSE event payloads */
export type TOCStreamEvent =
  | { event: "status"; data: { status: "uploading" | "toc_extracting" } }
  | { event: "toc_ready"; data: { entries: TOCEntry[] } }
  | { event: "error"; data: { message: string } };

export type JobStreamEvent =
  | { event: "phase_started"; data: { phase_name: string; phase_order: number } }
  | {
      event: "phase_completed";
      data: {
        phase_name: string;
        phase_order: number;
        output_md: string;
        tokens_input: number | null;
        tokens_output: number | null;
      };
    }
  | { event: "job_completed"; data: { job_id: string; download_url: string } }
  | { event: "error"; data: { phase_name?: string; message: string } };

export interface WorkerCapabilities {
  cli?: Record<string, boolean>;
  api?: Record<string, boolean>;
  code_version?: number | null;
  git_sha?: string | null;
}

export interface Worker {
  pc_id: string;
  last_heartbeat: string | null;
  status: string;
  notes: string | null;
  online: boolean;
  capabilities?: WorkerCapabilities | null;
}

export interface WorkerStatusResponse {
  pc_id: string;
  status: string;
}

export type BatchRollup = Partial<Record<JobStatus, number>>;

export interface BatchSummary {
  batch_id: string;
  book_id: string;
  subject: string;
  subject_variant?: string | null;
  grade: string | null;
  /** "homework" | "teacher_material" — see `JobKind`. */
  kind: JobKind;
  output_language: OutputLanguage;
  provider: string;
  model: string | null;
  transport: Transport;
  extract_transport: RoleTransport;
  judge_transport: RoleTransport;
  rollup: BatchRollup;
  lessons_covered: number;
  /** Whole-book TOC row count (display-only; not the rollup denominator). */
  toc_total: number;
  complete: boolean;
  created_at: string;
  // Cost-safety fields (C4): null when the batch is not paused by the budget monitor.
  // A polished cost-$ dashboard (showing batch_api_cost_usd prominently) defers to C6.
  paused_at: string | null;
  paused_reason: string | null;
  /** Per-batch Claude session-limit strategy (C5). */
  session_limit_strategy?: SessionLimitStrategy;
  /** Notion archive progress for the batch's done lessons (batch re-archive). */
  archived: number;
  unarchived: number;
  /** Archived lessons whose Notion page holds an OLDER job's output (regen husks). */
  stale: number;
}

export interface BatchLessonRow {
  job_id: string | null;
  toc_entry_id: string;
  section_title: string;
  order_index: number;
  status: JobStatus | null;
  attempts: number | null;
  current_phase: string | null;
  error_message: string | null;
  /** Classifier tag: "lesson" | "header" | "test" | "revision" | "practice" | "other". */
  toc_class: string;
}

/** Response from POST /jobs/batch/{id}/cancel */
export interface BatchCancelResponse {
  batch_id: string;
  cancelled: number;
  cancelling: number;
}

/** Response from POST /jobs/batch/{id}/resume */
export interface BatchResumeResponse {
  batch_id: string;
  jobs_resumed: number;
  /** job ids that carried a retired model (gemini-2.5, retired 2026-08-03) on
   *  one of their four role pairs — skipped rather than resumed. */
  jobs_skipped_retired: string[];
}

/** Response from POST /jobs/batch/{id}/retry-archive */
export interface BatchRearchiveResponse {
  batch_id: string;
  queued: number;
  already_running: boolean;
}

/** Response from POST /jobs/batch/{id}/pause and /unpause */
export interface BatchPauseResponse {
  batch_id: string;
  paused: boolean;
}

/** Response from POST /jobs/batch when preview=true */
export interface BatchPreviewResponse {
  book_id: string;
  preview: true;
  new: number;
  resumable: number;
  empty: number;
  /** Saved sections whose latest failed/cancelled job is pinned to a retired
   *  model (gemini-2.5, retired 2026-08-03) — DISJOINT from `resumable`: these
   *  can never be safely resumed (would call a dead model). Only
   *  `relaunch_mode: "discard"` can regenerate them. */
  retired: number;
  /** Count of TOC rows the launch would actually target (class-filtered). */
  target_count?: number;
  /** Rows excluded by class filtering, keyed by entry_class → count. */
  excluded_by_class?: Record<string, number>;
}

/** Normal (mutating) launch response — extends BatchSummary with per-launch tallies. */
export type BatchLaunchResponse = BatchSummary & {
  jobs_created: number;
  jobs_adopted: number;
  jobs_skipped: number;
  jobs_resumed: number;
};

/** Global launch defaults — singleton row in launch_defaults. Operator edits
 *  via PUT /api/v1/settings/launch-defaults; resolved at every batch/job launch. */
export interface LaunchDefaults {
  content_provider: string | null;
  content_model: string | null;
  content_transport: "cli" | "api" | null;
  judge_provider: string | null;
  judge_model: string | null;
  judge_transport: RoleTransport | null;
  solver_provider: string | null;
  solver_model: string | null;
  solver_transport: RoleTransport | null;
  solver_boss_arena_enabled: boolean;
  extract_provider: string | null;
  extract_model: string | null;
  extract_transport: RoleTransport | null;
  toc_transport: "cli" | "api" | null;
  /** Concrete global default for generated content language. */
  output_language: OutputLanguage | null;
}

/** A stored Google Cloud service-account key file. */
export interface SaKey {
  id: string;
  project_id: string;
  client_email: string;
  original_filename: string;
  label: string | null;
  byte_size: number;
  created_at: string | null;
  worker_count: number;
  /** Operator override for the fleet-wide per-credential api concurrency
   *  cap (BE-16). `null` = no override, falls back to the provider default.
   *  Project-wide: every sa_keys row sharing this key's project_id carries
   *  the same value (see PATCH /sa-keys/{key_id}). */
  max_concurrent_calls: number | null;
  /** Live in-flight slot count for this key's project credential. */
  slots_in_use: number;
  /** Resolved cap currently enforced for this key's project credential
   *  (the override above, or the provider default when none is set). */
  effective_limit: number;
}

/** Per-worker SA key assignment state served by GET /api/v1/sa-keys/assignments. */
export interface SaKeyAssignment {
  hostname: string;
  key_id: string | null;
  project_id: string | null;
  label: string | null;
  scrub: boolean;
}

/** One book's generation coverage, from GET /api/v1/dashboard/coverage. */
export interface CoverageEntry {
  grade: string | null;
  subject: string;
  book_id: string;
  book_status: BookStatus;
  source_language: string;
  original_filename: string;
  toc_validation: "verified" | "mismatch" | "skipped" | null;
  /** launchable lessons (TOC rows classified "lesson"), NOT raw TOC row count */
  lessons_total: number;
  done: number;
  running: number;
  pending: number;
  failed: number;
  cancelled: number;
  batch_id: string | null;
  paused: boolean;
}

export interface CoverageResponse {
  output_language: OutputLanguage;
  entries: CoverageEntry[];
}
