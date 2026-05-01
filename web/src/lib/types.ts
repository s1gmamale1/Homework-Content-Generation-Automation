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

export type JobStatus = "pending" | "running" | "done" | "failed";
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
  assembled_md: string | null;
  games_json: GamesPack | null;
  phases: PhaseOut[];
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
