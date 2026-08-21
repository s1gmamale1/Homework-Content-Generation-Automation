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
  /** "homework" | "teacher_material" — see `JobKind`. Routes the FE result link. */
  kind: JobKind;
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

/* ══════════════════════════════════════════════════════════════════════
 * Versioned homework regeneration — exact mirror of app/schemas/regeneration.py
 *
 * These are the Task 9 wire shapes and nothing else: every field name, every
 * nullable, every enum member matches the Pydantic model, so a rename on the
 * server shows up here as a type error rather than as `undefined` on a screen.
 * Timestamps are ISO-8601 strings (FastAPI serializes `datetime` that way);
 * UUIDs are strings.
 *
 * Everything the operator DECIDES from these shapes — polling, buckets, action
 * availability, error prose — lives in `api.ts`, not here.
 * ══════════════════════════════════════════════════════════════════════ */

/** `regeneration.OUTPUT_LANGUAGES`. Wider than Fleet's `OutputLanguage`
 *  aliasing on purpose: a regeneration lineage is scoped by
 *  `(toc_entry_id, output_language)` and the API validates against this set. */
export type RegenerationOutputLanguage = "uz" | "ru" | "en";

export const REGENERATION_OUTPUT_LANGUAGES: RegenerationOutputLanguage[] = ["uz", "ru", "en"];

/** `regeneration_states.CAMPAIGN_STATUSES`. */
export type RegenerationCampaignStatus =
  | "draft"
  | "canary_running"
  | "awaiting_canary_approval"
  | "approved"
  | "bulk_running"
  | "attention_required"
  | "completed"
  | "completed_with_abandonments"
  | "rejected"
  | "cancelled";

/** `regeneration_states.TARGET_STATUSES`. */
export type RegenerationTargetStatus =
  | "planned"
  | "generating"
  | "awaiting_canary_approval"
  | "publication_pending"
  | "publishing"
  | "published"
  | "generation_failed"
  | "publication_failed"
  | "abandoned";

/** `regeneration.BUCKETS` — the five design buckets plus `in_flight`, which
 *  exists so a report never silently drops a row that is still moving. */
export type RegenerationBucket =
  | "published"
  | "publication_pending"
  | "publication_failed"
  | "generation_failed"
  | "abandoned"
  | "in_flight";

/** `regeneration.publication_state`. The three `publication_failed` shapes are
 *  three different situations and split on `publication_next_attempt_at`. */
export type RegenerationPublicationState =
  | "published"
  | "abandoned"
  | "publishing"
  | "queued"
  | "backing_off"
  | "retry_due"
  | "action_required"
  | "not_started";

/* ── requests ─────────────────────────────────────────────────────────── */

/** `regeneration_contract.LaunchContract` — the DRAFT shape. A null role
 *  provider/model and `inherit` are legal operator inputs; the server resolves
 *  them once, at campaign creation. The content `model` is the exception: the
 *  server refuses a draft without one, so the UI must always send it. */
export interface RegenerationLaunchContract {
  provider: string;
  model: string | null;
  transport: Transport;
  extract_transport: RoleTransport;
  extract_provider: string | null;
  extract_model: string | null;
  judge_transport: RoleTransport;
  judge_provider: string | null;
  judge_model: string | null;
  solver_transport: RoleTransport;
  solver_provider: string | null;
  solver_model: string | null;
  session_limit_strategy: SessionLimitStrategy;
}

/** `regeneration.CampaignSelectionIn`. An empty list means "do not filter on
 *  this axis" — it is not "select nothing". */
export interface RegenerationSelection {
  book_ids: string[];
  toc_entry_ids: string[];
  output_languages: RegenerationOutputLanguage[];
}

/** `regeneration._PhaseSelectionIn`, shared by phase-plan/estimate/create. */
export interface RegenerationPhaseSelection {
  selected_phases: string[];
  excluded_affected_phases: string[];
  refresh_extraction: boolean;
  exclusion_acknowledged: boolean;
}

/** `regeneration.PhasePlanRequest`. */
export interface RegenerationPhasePlanRequest extends RegenerationPhaseSelection {
  subject: string;
}

/** `regeneration.EstimateRequest`. `extra="forbid"`: the create-only fields
 *  below must NOT appear in this body. */
export interface RegenerationEstimateRequest extends RegenerationPhaseSelection {
  selection: RegenerationSelection;
  contract: RegenerationLaunchContract;
  canary_size: number;
}

/** `regeneration.CreateCampaignRequest` — the estimate body plus the numbers
 *  the operator was SHOWN, echoed back so the campaign records what was
 *  approved rather than a figure recomputed at insert time. */
export interface RegenerationCampaignDraft extends RegenerationEstimateRequest {
  estimated_cost_low_usd: number | null;
  estimated_cost_high_usd: number | null;
  app_git_revision: string | null;
  actor: string;
  notes: Record<string, unknown>;
}

/** `regeneration.CampaignApproveRequest`. */
export interface RegenerationActorRequest {
  actor: string;
}

/** `regeneration._ReasonRequest` — reject / cancel / abandon. The server
 *  refuses a blank reason: it is stored as the audit record. */
export interface RegenerationReasonRequest extends RegenerationActorRequest {
  reason: string;
}

/* ── phase plan ───────────────────────────────────────────────────────── */

export interface RegenerationDependencyEdge {
  upstream: string;
  downstream: string;
}

/** `regeneration.PhasePlanOut`. `canonical_phases` starts with `extract` and
 *  is partitioned exactly by `regenerated_phases` + `copied_phases`. */
export interface RegenerationPhasePlan {
  subject: string;
  canonical_phases: string[];
  selected_phases: string[];
  auto_included_phases: string[];
  regenerated_phases: string[];
  copied_phases: string[];
  excluded_affected_phases: string[];
  broken_dependency_edges: RegenerationDependencyEdge[];
  refresh_extraction: boolean;
  regenerated_phase_count: number;
  copied_phase_count: number;
  acknowledgement_required: boolean;
  acknowledgement_message: string | null;
}

/** `regeneration.TargetPhasePlanOut` — the frozen per-target plan. */
export interface RegenerationTargetPhasePlan {
  selected_phases: string[];
  auto_included_phases: string[];
  regenerated_phases: string[];
  copied_phases: string[];
  excluded_affected_phases: string[];
  broken_dependency_edges: RegenerationDependencyEdge[];
  refresh_extraction: boolean;
}

/* ── discovery ────────────────────────────────────────────────────────── */

/** `regeneration.EligibleSourceOut`. */
export interface RegenerationEligibleSource {
  toc_entry_id: string;
  output_language: RegenerationOutputLanguage;
  source_job_id: string;
  book_id: string;
  subject: string;
  grade: string | null;
  source_publication_version: number;
  next_expected_version: number;
  source_is_revision: boolean;
  section_number: string | null;
  section_title: string;
  chapter_title: string;
  order_index: number;
  has_notion_lesson_page: boolean;
}

/** `regeneration.IneligibleLineageOut`. */
export interface RegenerationIneligibleLineage {
  toc_entry_id: string;
  output_language: RegenerationOutputLanguage;
  reasons: string[];
  detail: string;
}

/** `regeneration.EligibleSourcesOut`. */
export interface RegenerationEligibleSources {
  sources: RegenerationEligibleSource[];
  ineligible: RegenerationIneligibleLineage[];
  eligible_count: number;
  ineligible_count: number;
}

/** `regeneration.PreflightFailureOut` — one lesson with nowhere to publish. */
export interface RegenerationPreflightFailure {
  toc_entry_id: string;
  source_job_id: string | null;
  output_language: RegenerationOutputLanguage;
  subject: string;
  grade: string | null;
  lesson_title: string;
  reason: string;
  detail: string;
}

/** `regeneration.PreflightOut`. */
export interface RegenerationPreflight {
  ok: boolean;
  failure_count: number;
  failures: RegenerationPreflightFailure[];
}

/* ── estimate ─────────────────────────────────────────────────────────── */

/** `regeneration.EstimateLineOut`. `is_unpriced` (no RATE) and
 *  `is_static_envelope` (no VOLUME evidence) are INDEPENDENT markers. */
export interface RegenerationEstimateLine {
  budget: string;
  kind: string;
  phase: string;
  provider: string;
  model: string | null;
  calls_low: number;
  calls_high: number;
  unit_cost_usd: number;
  cost_low_usd: number;
  cost_high_usd: number;
  basis: string;
  observations: number;
  is_unpriced: boolean;
  is_observed: boolean;
  is_static_envelope: boolean;
}

/** `regeneration.RegenerationEstimateOut`. */
export interface RegenerationEstimateTotals {
  low_usd: number;
  high_usd: number;
  is_estimate: boolean;
  has_unpriced_lines: boolean;
  unpriced_line_count: number;
  is_complete: boolean;
  incomplete_reason: string | null;
  target_count: number;
  regenerated_phase_count: number;
  copied_phase_count: number;
  regenerated_extract_count: number;
  copied_extract_count: number;
  window_start: string;
  window_end: string;
  notes: string[];
  zero_volume_history_notes: string[];
  line_items: RegenerationEstimateLine[];
}

/** `regeneration.EstimateOut` — the whole read-only preview. */
export interface RegenerationEstimateResponse {
  target_count: number;
  canary_size: number;
  acknowledgement_required: boolean;
  sources: RegenerationEligibleSource[];
  ineligible: RegenerationIneligibleLineage[];
  phase_plans: RegenerationPhasePlan[];
  estimate: RegenerationEstimateTotals | null;
  preflight: RegenerationPreflight;
}

/* ── cost, provenance, lesson ─────────────────────────────────────────── */

/** `regeneration.ActualCostOut` — revision-job usage only. */
export interface RegenerationActualCost {
  usd: number;
  call_count: number;
  paid_call_count: number;
  zero_cost_marker_count: number;
  failed_call_count: number;
  excluded_row_count: number;
  revision_job_count: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cache_creation_tokens: number;
  total_tokens: number;
}

/** `regeneration.ProvenanceOut` — counted from the real phase rows. */
export interface RegenerationProvenance {
  copied_phase_count: number;
  regenerated_phase_count: number;
  phase_row_count: number;
}

/** `regeneration.LessonOut`. */
export interface RegenerationLesson {
  book_id: string | null;
  order_index: number | null;
  section_number: string | null;
  section_title: string | null;
  chapter_title: string | null;
}

/* ── targets ──────────────────────────────────────────────────────────── */

/** `regeneration.TargetReportOut`.
 *
 *  `reason` is always a full operator sentence — the API never asks the UI to
 *  interpret a status code. `action_required` is narrower than the campaign's
 *  `attention_required`: a publication the publisher will retry by itself
 *  needs no human. */
export interface RegenerationTargetReport {
  id: string;
  campaign_id: string;
  toc_entry_id: string;
  output_language: RegenerationOutputLanguage;
  is_canary: boolean;
  status: RegenerationTargetStatus;
  bucket: RegenerationBucket;
  publication_state: RegenerationPublicationState;
  is_terminal: boolean;
  action_required: boolean;
  reason: string;

  source_job_id: string | null;
  source_publication_version: number | null;
  source_note: string | null;

  revision_job_id: string | null;
  revision_job_status: JobStatus | null;
  revision_job_scheduled_at: string | null;
  /** `/api/v1/jobs/{id}` — the existing job-detail route. */
  content_path: string | null;
  /** `/api/v1/jobs/{id}/download` — the existing download route. */
  download_path: string | null;

  publication_version: number | null;
  notion_page_id: string | null;
  notion_page_url: string | null;
  publication_released_at: string | null;
  publication_attempts: number;
  publication_next_attempt_at: string | null;
  publication_last_error: string | null;
  /** The same value as `publication_last_error`, under the name a reader of an
   *  ABANDONED row looks for: what broke, beside why we stopped. */
  delivery_error: string | null;

  terminal_at: string | null;
  terminal_reason: string | null;
  abandon_requested_at: string | null;
  abandon_requested_reason: string | null;

  lesson: RegenerationLesson;
  phase_plan: RegenerationTargetPhasePlan | null;
  /** Set when this build cannot read the stored plan; the rest of the report
   *  still renders, because every other target still needs a decision. */
  phase_plan_error: string | null;
  judge_status_counts: Record<string, number>;
  solver_status_counts: Record<string, number>;
  copied_phase_count: number;
  regenerated_phase_count: number;

  created_at: string | null;
  updated_at: string | null;
}

/** `regeneration.CanaryOut` — where to read one canary before approving. */
export interface RegenerationCanary {
  target_id: string;
  toc_entry_id: string;
  output_language: RegenerationOutputLanguage;
  status: RegenerationTargetStatus;
  revision_job_id: string | null;
  revision_job_status: JobStatus | null;
  content_path: string | null;
  download_path: string | null;
  copied_phase_count: number;
  regenerated_phase_count: number;
  judge_status_counts: Record<string, number>;
  solver_status_counts: Record<string, number>;
}

/** `regeneration.ReleaseScheduleOut` — the ramp as it was PERSISTED. */
export interface RegenerationReleaseSchedule {
  job_count: number;
  wave_count: number;
  final_offset_seconds: number;
  first_scheduled_at: string | null;
  last_scheduled_at: string | null;
  source: string;
}

/** `regeneration.WaveFailureOut` — one target a release could not give a job.
 *  `current_status` is read back from the row, not taken from the message. */
export interface RegenerationWaveFailure {
  target_id: string;
  source_job_id: string | null;
  reason: string;
  current_status: RegenerationTargetStatus | null;
}

/* ── campaigns ────────────────────────────────────────────────────────── */

/** `regeneration.CampaignSummaryOut` — the list-row shape. */
export interface RegenerationCampaignSummary {
  id: string;
  status: RegenerationCampaignStatus;
  is_terminal: boolean;
  attention_required: boolean;
  target_count: number;
  status_counts: Record<string, number>;
  bucket_counts: Record<string, number>;
  canary_size: number;
  refresh_extraction: boolean;
  exclusion_acknowledged: boolean;
  requested_phases: string[];
  excluded_phases: string[];
  app_git_revision: string | null;
  estimated_cost_low_usd: number | null;
  estimated_cost_high_usd: number | null;
  canary_launched_at: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  cancel_requested_at: string | null;
  completed_at: string | null;
  rejected_reason: string | null;
  cancel_requested_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** `regeneration.CampaignListOut`. */
export interface RegenerationCampaignList {
  campaigns: RegenerationCampaignSummary[];
  count: number;
  limit: number;
  offset: number;
}

/** `regeneration.CampaignDetailOut` — the full report. */
export interface RegenerationCampaignDetail extends RegenerationCampaignSummary {
  selection_spec: Record<string, unknown>;
  launch_contract: Record<string, unknown>;
  /** The GLOBAL `settings.solver_enabled`, observed and reported — it is not a
   *  frozen per-campaign option, because no such job column exists. */
  solver_enabled_observed: boolean | null;
  buckets: Record<string, string[]>;
  targets: RegenerationTargetReport[];
  canary: RegenerationCanary[];
  actual_cost: RegenerationActualCost;
  judge_status_counts: Record<string, number>;
  solver_status_counts: Record<string, number>;
  provenance: RegenerationProvenance;
  release_schedule: RegenerationReleaseSchedule;
  warnings: string[];
  rollup_error: string | null;
  released_failures: RegenerationWaveFailure[];
}

/** `regeneration.TargetActionOut` — the response to a per-target mutation.
 *  The `previous_*` fields are captured BEFORE `retry_publication` clears the
 *  error, so they are the only surviving record of what prompted the retry. */
export interface RegenerationTargetActionResult {
  target: RegenerationTargetReport;
  campaign_id: string;
  campaign_status: RegenerationCampaignStatus;
  released_failures: RegenerationWaveFailure[];
  previous_publication_error: string | null;
  previous_publication_attempts: number | null;
  previous_publication_next_attempt_at: string | null;
}
