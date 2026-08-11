import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  AnswerKeySlide,
  ConclusionSlide,
  CoreIdeaSlide,
  CoverSlide,
  LessonMapSlide,
  ObjectivesSlide,
  PairWorkSlide,
  PassportSlide,
  QuizSlide,
  RubricSlide,
  StageSlide,
} from "@/components/deck";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api, ApiError } from "@/lib/api";
import { springSoft } from "@/lib/motion";
import type { TeacherDeck, TeacherDeckStage } from "@/lib/types";
import { BACK_PILL, GLASS_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

/** One entry in the assembled 18-slide pager. */
interface SlideEntry {
  key: string;
  /** Short label for the chip nav / footer. */
  label: string;
  node: React.ReactNode;
}

/**
 * Assembles the pager's slide order from a `TeacherDeck`:
 *   cover → passport → objectives → core_idea → lesson_map
 *   → stages 1–4 (teacher/hook/video/anchors)
 *   → quiz[0..4] (5 slides) → answer_key
 *   → pair-work stage → conclusion/yakun stage → rubric
 *
 * `deck.stages` carries all 7 lesson-map stages, including the "Kviz" one —
 * that stage is NOT rendered as a generic StageSlide. It's identified (by
 * title, falling back to position) and expanded in place into the 5
 * QuizSlides + the AnswerKeySlide, since `deck.quiz`/`deck.answer_key` are
 * the structured sources for that content. The pair-work and
 * conclusion/yakun stages likewise borrow their header (bosqich #, minutes,
 * title) from `deck.stages` but render body content from the dedicated
 * `deck.pair_work` / `deck.conclusion` objects.
 */
function assembleSlides(deck: TeacherDeck): SlideEntry[] {
  const sortedStages = [...deck.stages].sort((a, b) => a.index - b.index);
  const headStages = sortedStages.slice(0, 4);
  const tailStages = sortedStages.slice(4);

  const quizStage: TeacherDeckStage | undefined =
    tailStages.find((s) => /kviz/i.test(s.title)) ?? tailStages[0];
  const remaining = tailStages.filter((s) => s !== quizStage);
  const pairWorkStage: TeacherDeckStage | undefined =
    remaining.find((s) => /juft/i.test(s.title)) ?? remaining[0];
  const conclusionStage: TeacherDeckStage | undefined =
    remaining.find((s) => s !== pairWorkStage) ?? remaining[1];

  const slides: SlideEntry[] = [
    {
      key: "cover",
      label: "Muqova",
      node: <CoverSlide meta={deck.meta} passport={deck.passport} stageCount={deck.stages.length} />,
    },
    { key: "passport", label: "Pasport", node: <PassportSlide passport={deck.passport} /> },
    { key: "objectives", label: "Maqsad", node: <ObjectivesSlide objectives={deck.objectives} /> },
    { key: "core_idea", label: "G'oya", node: <CoreIdeaSlide coreIdea={deck.core_idea} /> },
    { key: "lesson_map", label: "Xarita", node: <LessonMapSlide items={deck.lesson_map} /> },
  ];

  for (const stage of headStages) {
    slides.push({
      key: `stage-${stage.index}`,
      label: `${stage.index}-bosqich`,
      node: <StageSlide stage={stage} accent={stage.badge === "ekranga" ? "orange" : "blue"} />,
    });
  }

  const quizTotal = deck.quiz.length;
  deck.quiz.forEach((item) => {
    slides.push({
      key: `quiz-${item.number}`,
      label: `Savol ${item.number}`,
      node: <QuizSlide item={item} total={quizTotal} />,
    });
  });

  slides.push({
    key: "answer_key",
    label: "Kalit",
    node: <AnswerKeySlide items={deck.answer_key} stageIndex={quizStage?.index ?? deck.stages.length} />,
  });

  if (pairWorkStage) {
    slides.push({
      key: "pair_work",
      label: pairWorkStage.title,
      node: <PairWorkSlide stage={pairWorkStage} pairWork={deck.pair_work} />,
    });
  }

  if (conclusionStage) {
    slides.push({
      key: "conclusion",
      label: conclusionStage.title,
      node: <ConclusionSlide stage={conclusionStage} conclusion={deck.conclusion} />,
    });
  }

  slides.push({ key: "rubric", label: "Baholash", node: <RubricSlide rubric={deck.rubric} /> });

  return slides;
}

function DeckPager({ deck }: { deck: TeacherDeck }) {
  const slides = useMemo(() => assembleSlides(deck), [deck]);
  const [active, setActive] = useState(0);
  const [dir, setDir] = useState(1);
  const reduce = useReducedMotion();

  const idx = Math.min(active, slides.length - 1);
  const slide = slides[idx];
  const prev = idx > 0 ? slides[idx - 1] : null;
  const next = idx < slides.length - 1 ? slides[idx + 1] : null;

  function go(i: number) {
    if (i === idx) return;
    setDir(i > idx ? 1 : -1);
    setActive(i);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const offset = reduce ? 0 : 28;
  const variants = {
    enter: (d: number) => ({ opacity: 0, x: d * offset }),
    center: { opacity: 1, x: 0 },
    exit: (d: number) => ({ opacity: 0, x: d * -offset }),
  };

  return (
    <div className="mt-7 space-y-5">
      <nav className="flex flex-wrap gap-2" aria-label="Slides">
        {slides.map((s, i) => {
          const isActive = i === idx;
          return (
            <motion.button
              key={s.key}
              type="button"
              onClick={() => go(i)}
              aria-current={isActive ? "page" : undefined}
              whileTap={{ scale: 0.95 }}
              transition={{ type: "spring", stiffness: 400, damping: 25 }}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
                isActive
                  ? "border-[#5b8dff]/70 bg-[#5b8dff]/15 text-white"
                  : "border-white/[0.1] bg-white/[0.04] text-white/65 hover:border-white/[0.2] hover:bg-white/[0.08] hover:text-white",
              )}
            >
              <span className={cn("font-mono", isActive ? "text-white/70" : "text-white/40")}>
                {String(i + 1).padStart(2, "0")}
              </span>
              {s.label}
            </motion.button>
          );
        })}
      </nav>

      <AnimatePresence mode="wait" custom={dir} initial={false}>
        <motion.div
          key={idx}
          custom={dir}
          variants={variants}
          initial="enter"
          animate="center"
          exit="exit"
          transition={springSoft}
        >
          {slide.node}
        </motion.div>
      </AnimatePresence>

      <footer className="flex items-center justify-between gap-3">
        {prev ? (
          <motion.button
            type="button"
            onClick={() => go(idx - 1)}
            whileTap={{ scale: 0.96 }}
            className={cn(GLASS_BTN, "px-3.5 py-2 text-xs")}
          >
            <ArrowLeft className="size-3.5" />
            {prev.label}
          </motion.button>
        ) : (
          <span />
        )}

        <span className="shrink-0 text-xs text-white/40">
          {idx + 1} / {slides.length}
        </span>

        {next ? (
          <motion.button
            type="button"
            onClick={() => go(idx + 1)}
            whileTap={{ scale: 0.96 }}
            className={cn(GLASS_BTN, "px-3.5 py-2 text-xs")}
          >
            {next.label}
            <ArrowRight className="size-3.5" />
          </motion.button>
        ) : (
          <span />
        )}
      </footer>
    </div>
  );
}

export function DeckPage() {
  const { id } = useParams<{ id: string }>();
  const {
    data: deck,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["deck", id],
    queryFn: () => (id ? api.getDeck(id) : Promise.reject(new Error("no id"))),
    enabled: Boolean(id),
    retry: (count, err) => (err instanceof ApiError && err.status === 404 ? false : count < 1),
  });

  if (isLoading) {
    return (
      <div className="relative min-h-[calc(100vh-9rem)]">
        <SpaceBackdrop />
        <div className="relative z-10 flex items-center gap-2 text-sm text-white/60">
          <Loader2 className="size-4 animate-spin text-[#5b8dff]" /> Loading teacher deck…
        </div>
      </div>
    );
  }

  if (error || !deck) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <div className="relative min-h-[calc(100vh-9rem)]">
        <SpaceBackdrop />
        <div className="relative z-10">
          <h1 className="text-3xl font-semibold tracking-tight text-white">Not ready</h1>
          <p className="mt-2 text-sm text-white/55">
            {notFound
              ? "This job isn't a teacher-material deck, or its content hasn't been generated yet."
              : "Couldn't load the teacher deck."}
          </p>
          <Link to={`/job/${id}`} className={cn(BACK_PILL, "mt-6")}>
            <ArrowLeft className="size-4" /> Back to job
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10">
        <div className="flex items-center justify-between gap-3">
          <Link to={`/job/${id}`} className={BACK_PILL}>
            <ArrowLeft className="size-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
            Back to job
          </Link>
        </div>

        <h1 className="mt-6 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Teacher deck
        </h1>
        <p className="mt-2 flex flex-wrap items-center gap-x-2 font-mono text-[0.72rem] uppercase tracking-[0.16em] text-white/50">
          <span>{deck.meta.subject_label}</span>
          <span className="text-white/25">·</span>
          <span>{deck.meta.grade}-sinf</span>
          <span className="text-white/25">·</span>
          <span>{deck.meta.topic_number}-mavzu</span>
        </p>

        <DeckPager deck={deck} />
      </div>
    </div>
  );
}
