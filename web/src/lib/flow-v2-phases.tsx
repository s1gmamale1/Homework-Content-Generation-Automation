import type { ReactNode } from "react";
import { CaseBasedPreviewView } from "@/components/flow-v2/case-based-preview";
import { MemoryCheckView } from "@/components/flow-v2/memory-check";
import { BossArenaView } from "@/components/flow-v2/boss-arena";
import { RealLifeChallengeView } from "@/components/flow-v2/practice-rlc";
import { ErrorDetectionView } from "@/components/flow-v2/practice-error-detection";
import { CbpModeGameView } from "@/components/flow-v2/cbp-mode-game";
import { FlashcardDeck } from "@/components/flashcards/flashcard-deck";
import type {
  BossArena, CaseBasedPreview, CbpModeGame, ErrorDetection, FlashcardsPack,
  Job, MemoryCheckPack, RealLifeChallenge,
} from "@/lib/types";

export type Division = "Learning Sections" | "Practice Arc" | "Boss Arena";

export interface FlowV2PhaseDef {
  key: string;
  column: keyof Job;
  title: string;
  division: Division;
  isEmpty: (data: unknown) => boolean;
  render: (data: any) => ReactNode;
}

const emptyArr = (k: string) => (d: unknown) => !d || !(d as Record<string, unknown[]>)[k]?.length;
const gameDef = (title: string, column: keyof Job): FlowV2PhaseDef => ({
  key: column as string, column, title, division: "Practice Arc",
  isEmpty: (d) => !d,
  render: (d: CbpModeGame) => <CbpModeGameView game={d} />,
});

export const FLOW_V2_PHASES: FlowV2PhaseDef[] = [
  { key: "cbp", column: "cbp_json", title: "Case-Based Preview", division: "Learning Sections",
    isEmpty: (d) => !d, render: (d: CaseBasedPreview) => <CaseBasedPreviewView cbp={d} /> },
  { key: "flashcards", column: "flashcards_json", title: "Flashcards", division: "Learning Sections",
    isEmpty: emptyArr("cards"), render: (d: FlashcardsPack) => <FlashcardDeck cards={d.cards ?? []} /> },
  { key: "memory-check", column: "memory_check_json", title: "Memory Check", division: "Learning Sections",
    isEmpty: emptyArr("items"), render: (d: MemoryCheckPack) => <MemoryCheckView pack={d} /> },
  { key: "practice-rlc", column: "practice_rlc_json", title: "Real-Life Challenge", division: "Practice Arc",
    isEmpty: (d) => !d, render: (d: RealLifeChallenge) => <RealLifeChallengeView rlc={d} /> },
  { key: "practice-error-detection", column: "practice_error_detection_json", title: "Error Detection", division: "Practice Arc",
    isEmpty: (d) => !d, render: (d: ErrorDetection) => <ErrorDetectionView ed={d} /> },
  gameDef("Memory Match", "practice_memory_match_json"),
  gameDef("TicTacToe", "practice_tictactoe_json"),
  gameDef("Jigsaw Matching", "practice_jigsaw_json"),
  gameDef("Sentence Filling", "practice_sentence_json"),
  { key: "boss-arena", column: "boss_arena_json", title: "Boss Arena", division: "Boss Arena",
    isEmpty: emptyArr("questions"), render: (d: BossArena) => <BossArenaView boss={d} /> },
];

export const DIVISION_ORDER: Division[] = ["Learning Sections", "Practice Arc", "Boss Arena"];

/** A job is Flow v2 if any Flow v2 column is populated. */
export function isFlowV2(job: Job): boolean {
  return Boolean(
    job.cbp_json || job.boss_arena_json || job.source_map_json ||
    job.memory_check_json || job.practice_rlc_json || job.practice_error_detection_json ||
    job.practice_memory_match_json || job.practice_tictactoe_json ||
    job.practice_jigsaw_json || job.practice_sentence_json,
  );
}
