import {
  Check,
  Heart,
  Lightbulb,
  RefreshCcw,
  Skull,
  Sparkles,
  Sword,
  Trophy,
  X,
} from "lucide-react";
import { useState } from "react";
import type { BossQuestion, FinalChallenge } from "@/lib/types";
import { cn } from "@/lib/utils";

interface BossFightProps {
  challenge: FinalChallenge;
}

interface PerQuestionState {
  picked: number | null;
  open_value: string;
  hints_used: number;
  result: "correct" | "wrong" | null;
}

export function BossFight({ challenge }: BossFightProps) {
  const startHp = challenge.starting_hp || 100;
  const questions = challenge.questions;

  const [hp, setHp] = useState(startHp);
  const [pos, setPos] = useState(0);
  const [scored, setScored] = useState({ correct: 0, total: 0 });
  const [perQ, setPerQ] = useState<Record<number, PerQuestionState>>({});

  const q = questions[pos];
  const state = perQ[pos] ?? {
    picked: null,
    open_value: "",
    hints_used: 0,
    result: null,
  };
  const answered = state.result !== null;
  const dead = hp <= 0;
  const finished = pos >= questions.length - 1 && answered;
  const won = finished && hp > 0;

  function setQ(update: Partial<PerQuestionState>) {
    setPerQ((m) => ({ ...m, [pos]: { ...state, ...update } }));
  }

  function takeHit(damage: number) {
    setHp((h) => Math.max(0, h - damage));
  }

  function judgeMcLike(idx: number) {
    if (!q || answered) return;
    const correct = q.correct_index === idx;
    const dmg = correct ? 0 : (q.damage ?? 20);
    setQ({ picked: idx, result: correct ? "correct" : "wrong" });
    setScored((s) => ({
      correct: s.correct + (correct ? 1 : 0),
      total: s.total + 1,
    }));
    if (dmg > 0) takeHit(dmg);
  }

  function judgeOpen() {
    if (!q || answered) return;
    const expected = (q.correct_answer ?? "").trim().toLowerCase();
    const actual = state.open_value.trim().toLowerCase();
    const correct = Boolean(expected) && actual === expected;
    const dmg = correct ? 0 : (q.damage ?? 20);
    setQ({ result: correct ? "correct" : "wrong" });
    setScored((s) => ({
      correct: s.correct + (correct ? 1 : 0),
      total: s.total + 1,
    }));
    if (dmg > 0) takeHit(dmg);
  }

  function useHint() {
    if (!q || answered) return;
    if (!q.hints || q.hints.length === 0) return;
    if (state.hints_used >= q.hints.length) return;
    setQ({ hints_used: state.hints_used + 1 });
    takeHit(5);
  }

  function next() {
    setPos((i) => Math.min(questions.length - 1, i + 1));
  }

  function reset() {
    setHp(startHp);
    setPos(0);
    setScored({ correct: 0, total: 0 });
    setPerQ({});
  }

  if (questions.length === 0) {
    return <p className="text-sm text-(--color-ink-muted)">No boss questions.</p>;
  }
  if (!q) return null;

  const hpPct = Math.max(0, Math.min(100, (hp / startHp) * 100));
  const hpColor =
    hpPct > 60 ? "var(--color-success)" : hpPct > 25 ? "var(--color-amber)" : "var(--color-error)";

  return (
    <div className="flex flex-col gap-5">
      {/* HP bar / status */}
      <div className="flex flex-col gap-2 rounded-(--radius-md) border border-(--color-border) bg-(--color-canvas) p-4">
        <div className="flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-(--color-ink-muted)">
            <Heart className="size-3.5" style={{ color: hpColor }} />
            HP {hp} / {startHp}
          </span>
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            Q {Math.min(pos + 1, questions.length)} / {questions.length}
          </span>
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            {scored.correct}/{scored.total} ✓
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-(--color-elevated)">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${hpPct}%`, background: hpColor }}
          />
        </div>
      </div>

      {/* End-state banners */}
      {dead && <EndBanner kind="defeat" onReset={reset} score={scored} />}
      {!dead && won && <EndBanner kind="victory" onReset={reset} score={scored} />}

      {!dead && !won && (
        <>
          {/* Question header tags */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_10%)] px-2.5 py-0.5 font-mono text-[0.62rem] uppercase tracking-[0.16em] text-(--color-error)">
              <Sword className="size-3" />−{q.damage ?? 20} HP
            </span>
            {q.bloom_level && (
              <span className="rounded-full border border-(--color-border) bg-(--color-elevated) px-2.5 py-0.5 font-mono text-[0.62rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
                Bloom {q.bloom_level}
              </span>
            )}
            {q.pisa_level && (
              <span className="rounded-full border border-(--color-border) bg-(--color-elevated) px-2.5 py-0.5 font-mono text-[0.62rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
                PISA {q.pisa_level}
              </span>
            )}
          </div>

          <p className="text-base font-medium leading-relaxed text-(--color-ink)">{q.prompt}</p>

          {/* Answer UI by kind */}
          {q.kind === "open" ? (
            <OpenAnswer
              value={state.open_value}
              answered={answered}
              expected={q.correct_answer ?? ""}
              onChange={(v) => setQ({ open_value: v })}
              onSubmit={judgeOpen}
              correct={state.result === "correct"}
            />
          ) : (
            <McLikeOptions
              question={q}
              picked={state.picked}
              answered={answered}
              onPick={judgeMcLike}
            />
          )}

          {/* Hint ladder */}
          {!answered && q.hints && q.hints.length > 0 && (
            <HintLadder hints={q.hints} used={state.hints_used} onUse={useHint} />
          )}

          {answered && q.explanation && (
            <div className="rounded-(--radius-md) border border-(--color-border) bg-(--color-canvas) px-3.5 py-2.5 text-sm leading-relaxed text-(--color-ink-soft)">
              <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                {state.result === "correct" ? "Correct" : "Not quite"} ·{" "}
              </span>
              {q.explanation}
            </div>
          )}

          {answered && pos < questions.length - 1 && (
            <button
              type="button"
              onClick={next}
              className="self-start rounded-(--radius-sm) bg-(--color-accent) px-4 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)"
            >
              Continue →
            </button>
          )}
        </>
      )}
    </div>
  );
}

function McLikeOptions({
  question,
  picked,
  answered,
  onPick,
}: {
  question: BossQuestion;
  picked: number | null;
  answered: boolean;
  onPick: (i: number) => void;
}) {
  const correctIdx = question.correct_index ?? null;
  return (
    <div className="flex flex-col gap-2">
      {(question.options ?? []).map((opt, i) => {
        const isPicked = picked === i;
        const isAnswer = correctIdx !== null && i === correctIdx;
        const reveal = answered && (isPicked || isAnswer);
        return (
          <button
            key={i}
            type="button"
            onClick={() => onPick(i)}
            disabled={answered}
            className={cn(
              "rounded-(--radius-md) border px-3.5 py-2.5 text-left text-sm transition-colors",
              "disabled:cursor-default",
              !answered &&
                "border-(--color-border) bg-(--color-elevated) hover:border-(--color-border-hover) hover:bg-(--color-elevated-hover) cursor-pointer",
              reveal &&
                isAnswer &&
                "border-[oklch(0.78_0.10_145_/_50%)] bg-[oklch(0.78_0.10_145_/_10%)] text-(--color-success)",
              reveal &&
                isPicked &&
                !isAnswer &&
                "border-[oklch(0.70_0.16_25_/_50%)] bg-[oklch(0.70_0.16_25_/_10%)] text-(--color-error)",
              answered && !reveal && "opacity-50",
            )}
          >
            <span className="flex items-center gap-2.5">
              {reveal && isAnswer && <Check className="size-3.5" />}
              {reveal && isPicked && !isAnswer && <X className="size-3.5" />}
              <span>{opt}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function OpenAnswer({
  value,
  answered,
  expected,
  onChange,
  onSubmit,
  correct,
}: {
  value: string;
  answered: boolean;
  expected: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  correct: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={answered}
        placeholder="Write your answer…"
        className="min-h-24 w-full rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3.5 py-2.5 text-sm text-(--color-ink) outline-none transition-colors hover:border-(--color-border-hover) focus:border-(--color-accent) focus:ring-2 focus:ring-(--color-accent)/30 disabled:opacity-60"
      />
      {!answered ? (
        <button
          type="button"
          onClick={onSubmit}
          disabled={!value.trim()}
          className="self-start rounded-(--radius-sm) bg-(--color-accent) px-4 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep) disabled:opacity-50"
        >
          Submit answer
        </button>
      ) : (
        <div
          className={cn(
            "rounded-(--radius-md) border px-3.5 py-2.5 text-sm",
            correct
              ? "border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_8%)] text-(--color-success)"
              : "border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] text-(--color-error)",
          )}
        >
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em]">
            {correct ? "Accepted" : "Expected"} ·{" "}
          </span>
          {expected || "—"}
        </div>
      )}
    </div>
  );
}

function HintLadder({
  hints,
  used,
  onUse,
}: {
  hints: string[];
  used: number;
  onUse: () => void;
}) {
  return (
    <div className="rounded-(--radius-md) border border-dashed border-(--color-border-hover) bg-(--color-elevated) p-3">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="inline-flex items-center gap-1.5 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          <Lightbulb className="size-3" />
          Hints {used} / {hints.length}
        </span>
        {used < hints.length && (
          <button
            type="button"
            onClick={onUse}
            className="rounded-(--radius-sm) border border-(--color-border-hover) bg-(--color-elevated) px-2 py-0.5 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:bg-(--color-accent-soft) hover:text-(--color-accent)"
          >
            Reveal next −5 HP
          </button>
        )}
      </div>
      <div className="flex flex-col gap-1.5">
        {hints.slice(0, used).map((h, i) => (
          <p
            key={i}
            className="rounded-sm border-l-2 border-(--color-accent) bg-(--color-accent-soft)/50 px-2 py-1 text-[0.85rem] leading-snug text-(--color-ink-soft)"
          >
            {h}
          </p>
        ))}
      </div>
    </div>
  );
}

function EndBanner({
  kind,
  onReset,
  score,
}: {
  kind: "victory" | "defeat";
  onReset: () => void;
  score: { correct: number; total: number };
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 rounded-(--radius-md) border px-4 py-3",
        kind === "victory"
          ? "border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_8%)]"
          : "border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)]",
      )}
    >
      <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em]">
        {kind === "victory" ? (
          <>
            <Trophy className="size-3.5 text-(--color-success)" />
            <span className="text-(--color-success)">
              Victory · {score.correct}/{score.total}
            </span>
            <Sparkles className="size-3 text-(--color-accent)" />
          </>
        ) : (
          <>
            <Skull className="size-3.5 text-(--color-error)" />
            <span className="text-(--color-error)">
              Defeated · {score.correct}/{score.total}
            </span>
          </>
        )}
      </span>
      <button
        type="button"
        onClick={onReset}
        className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover)"
      >
        <RefreshCcw className="size-3" /> Restart
      </button>
    </div>
  );
}
