/* Mirrors the Pydantic schemas in app/schemas/. Keep in sync if the API changes. */

export type Subject =
  | "biology"
  | "english"
  | "geometriya-g7-11"
  | "history"
  | "kimyo-g7-11"
  | "math-algebra"
  | "physics";

export const SUBJECTS: Subject[] = [
  "biology",
  "english",
  "geometriya-g7-11",
  "history",
  "kimyo-g7-11",
  "math-algebra",
  "physics",
];

export type BookStatus = "uploading" | "toc_extracting" | "toc_ready" | "failed";

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
}

export interface Book {
  id: string;
  subject: Subject;
  original_filename: string;
  status: BookStatus;
  error_message: string | null;
  gemini_file_expires_at: string | null;
  file_size_bytes: number | null;
  created_at: string | null;
  toc: TOCEntry[] | null;
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

export type JobStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "cancelling"
  | "cancelled";
export type Difficulty = "easy" | "hard";

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
}

export interface Job {
  id: string;
  book_id: string;
  toc_entry_id: string;
  subject: Subject;
  difficulty: Difficulty | null;
  status: JobStatus;
  current_phase: string | null;
  error_message: string | null;
  phases: PhaseOut[];
  provider?: string;
  model?: string | null;
  notion_skip_reason?: string | null;
}

export interface ProviderModelManifest {
  providers: Record<string, string[]>;
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
  | { event: "difficulty_classified"; data: { difficulty: Difficulty } }
  | { event: "job_completed"; data: { job_id: string; download_url: string } }
  | { event: "error"; data: { phase_name?: string; message: string } };
