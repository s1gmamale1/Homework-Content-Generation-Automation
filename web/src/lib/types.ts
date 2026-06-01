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
  flashcards_json: FlashcardsPack | null;
  final_challenge_json: FinalChallenge | null;
  memory_sprint_json: MemorySprintPack | null;
  reading_json: ReadingPassage | null;
  source_map_json: SourceMap | null;
  cbp_json: CaseBasedPreview | null;
  memory_check_json: MemoryCheckPack | null;
  boss_arena_json: BossArena | null;
  practice_rlc_json: RealLifeChallenge | null;
  practice_error_detection_json: ErrorDetection | null;
  practice_memory_match_json: CbpModeGame | null;
  practice_tictactoe_json: CbpModeGame | null;
  practice_jigsaw_json: CbpModeGame | null;
  practice_sentence_json: CbpModeGame | null;
  phases: PhaseOut[];
  provider?: string;
  model?: string | null;
}

export interface ProviderModelManifest {
  providers: Record<string, string[]>;
}

/* /api/v1/agent/stats — per-provider rolling consumption against caps */
export interface ProviderStatsWindow {
  calls: number;
  duration_secs: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  success_pct: number;
  limit_calls_per_window: number | null;
  pct_of_limit: number | null;
}
export interface AgentStats {
  windows: string[];
  providers: Record<string, Record<string, ProviderStatsWindow>>;
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

/* ───────── Flow v2 (app/schemas/) ───────── */

/* Source Map (flow_v2.py :: SourceMap / SourceConcept) */
export type SourceConceptKind = "concept" | "term" | "formula" | "process" | "skill" | "fact";
export interface SourceConcept {
  id: string; label: string; statement: string;
  kind?: SourceConceptKind; source_ref?: string | null;
}
export interface SourceMap {
  subject_family: string; chapter: string; section: string; concepts: SourceConcept[];
}

/* Case-Based Preview (flow_v2.py :: CaseBasedPreview) */
export type CheckpointIntent = "identify" | "decide" | "justify_or_avoid_mistake";
export type CheckpointForm = "mcq" | "choice" | "short_select" | "true_false";
export interface CaseSetup { narrative: string; student_role: string; task: string; }
export interface CaseCheckpoint {
  intent: CheckpointIntent; form: CheckpointForm; question: string;
  options?: string[]; correct_index?: number | null; feedback: string;
}
export interface DecisionProcessExplanation {
  prompt: string; expected_components: ("concept" | "method" | "mistake")[];
  rubric: Record<string, unknown>; sample_acceptable_answer: string;
  eval_mode?: "ai" | "rubric_ai"; min_chars?: number; options?: null;
}
export interface CaseSimulation { correct_path: string; wrong_path: string; why_wrong_fails: string; }
export interface FeedbackSummary { understood: string; mistake: string; review: string; }
export interface CompletionRules { pass_condition: string; retry_condition: string; }
export interface LearningBlock {
  explanation: string; title?: string | null; visual_svg?: string | null; source_concept_id?: string | null;
}
export interface CaseBasedPreview {
  title: string; student_role: string; case_type: string; source_concept_ids: string[];
  case_setup: CaseSetup; checkpoints: CaseCheckpoint[];
  learning_block_1: LearningBlock; learning_block_2: LearningBlock;
  decision_process_explanation: DecisionProcessExplanation;
  final_simulation: CaseSimulation; feedback_summary: FeedbackSummary; completion_rules: CompletionRules;
}

/* CBP-mode game — COMPACT standalone (practice_games.py :: CbpModeGame, Path B). NOT a CBP. */
export type PracticeInteractionMode = "memory_match" | "jigsaw" | "sentence_fill" | "tictactoe";
export interface GameChoice { label: string; is_correct?: boolean; reason?: string | null; }
export interface MemoryMatchPair { left: string; right: string; }
export interface MemoryMatchPayload { pairs: MemoryMatchPair[]; }
export interface JigsawPiece { id: string; content: string; }
export interface JigsawPayload { pieces: JigsawPiece[]; allowed_assembly_types: string[]; }
export interface SentenceFillPayload { sentence: string; chips: GameChoice[]; }
export interface TicTacToePayload { cells: GameChoice[]; }
export type InteractionPayload =
  | MemoryMatchPayload | JigsawPayload | SentenceFillPayload | TicTacToePayload;
export interface CbpModeGame {
  title: string;
  source_concept_ids: string[];
  interaction_mode: PracticeInteractionMode;
  instruction: string;
  interaction_payload: InteractionPayload;
  why_prompt: string;
  expected_reasoning_keywords?: string[];
}

/* Memory Check (memory_check.py :: MemoryCheckPack) */
export type MemoryCheckKind = "multiple_choice" | "fill_blank" | "choose_correct_explanation";
export interface MemoryCheckOption { text: string; is_correct?: boolean; reason?: string | null; }
export interface MemoryCheckBlank { answer: string; accepted_variations?: string[]; }
export interface MemoryCheckItem {
  flashcard_id: string; kind: MemoryCheckKind; prompt: string;
  options?: MemoryCheckOption[]; blanks?: MemoryCheckBlank[];
  why_prompt?: string | null; expected_reasoning_keywords?: string[];
  correct_feedback?: string | null; wrong_feedback?: string | null; explanation?: string | null;
}
export interface MemoryCheckPack { items: MemoryCheckItem[]; pass_threshold?: number; }

/* Boss Arena (boss_arena.py :: BossArena / BossQuestion) — distinct from legacy FinalChallenge */
export interface BossArenaQuestion {
  concept_ids: string[]; difficulty: "easy" | "medium" | "hard";
  scenario: string; why: string; how: string; what: string;
  bloom_level?: string | null; pisa_level?: string | null;
  base_damage?: number; hints?: string[];
  correct_feedback: string; partial_feedback: string; wrong_feedback: string;
}
export interface BossArena { title?: string; starting_hp?: number; questions: BossArenaQuestion[]; }

/* Real-Life Challenge (practice_games.py :: RealLifeChallenge) */
export interface RlcDecision {
  question: string; options: string[]; correct_option: number;
  why_required?: boolean; confidence_required?: boolean; expected_reasoning?: string[];
  correct_feedback: string; partial_feedback: string; wrong_feedback: string;
}
export interface RealLifeChallenge {
  scenario_id?: string | null; concept_ids: string[]; role: string; task: string;
  grade_band?: string | null; pisa?: string | null; context: string; prediction_prompt: string;
  decisions: RlcDecision[]; red_herring?: string | null; final_summary: string;
}

/* Error Detection (practice_games.py :: ErrorDetection) */
export type ErrorPattern = "math_equation" | "grammar_sentence" | "science_diagram";
export interface ErrorBlock { id: string; content: string; is_error?: boolean; }
export interface ErrorDetection {
  task_id?: string | null; pattern: ErrorPattern; concept_ids: string[];
  grade_band?: string | null; difficulty?: string | null; blocks: ErrorBlock[];
  correct_answer_for_error_block: string; accepted_variants?: string[];
  common_mistake_source?: string; hint: string; why_prompt?: string;
  expected_reasoning_keywords?: string[];
  correct_feedback: string; wrong_correction_feedback: string; reveal_feedback: string;
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
