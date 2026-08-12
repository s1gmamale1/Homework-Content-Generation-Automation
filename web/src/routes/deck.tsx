import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Download, Loader2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  AnswerKeySlide,
  CoreIdeaSlide,
  CoverSlide,
  LessonMapSlide,
  ObjectivesSlide,
  PassportSlide,
  QuizSlide,
  RubricSlide,
  StageSlide,
} from "@/components/deck";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api, ApiError } from "@/lib/api";
import { springSoft } from "@/lib/motion";
import type { TeacherDeck, TeacherDeckMeta } from "@/lib/types";
import { BACK_PILL, GLASS_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

/**
 * Builds a filename-friendly document title from a deck's `meta`, e.g.
 * `11-sinf 19-mavzu 1991–2017-yillarda Hindiston Respublikasi`.
 * Any segment whose source field is empty/missing is dropped (never
 * renders `undefined`/`null`); filesystem-hostile characters (`/`, `\`,
 * newlines) are replaced with `-` since Chrome/Safari derive the print/PDF
 * suggested filename from `document.title`.
 */
export function deckPdfTitle(meta: TeacherDeckMeta): string {
  const segments: string[] = [];
  if (meta.grade) segments.push(`${meta.grade}-sinf`);
  if (meta.topic_number != null) segments.push(`${meta.topic_number}-mavzu`);
  if (meta.topic_title) segments.push(meta.topic_title);
  return segments.join(" ").replace(/[/\\\r\n]+/g, "-");
}

/** One entry in the assembled slide pager. */
interface SlideEntry {
  key: string;
  /** Short label for the chip nav / footer. */
  label: string;
  node: React.ReactNode;
}

/**
 * Assembles the pager's slide order from a `TeacherDeck`:
 *   cover → passport → objectives → core_idea → lesson_map
 *   → every `stages[]` entry (sorted by `index`), as a StageSlide, in full
 *   → quiz[] (one QuizSlide per item) → answer_key
 *   → rubric
 *
 * Deliberately GENERIC and content-complete — no assumption about stage
 * count, quiz count, or language:
 *  - EVERY stage renders, in `index` order. No slicing to a fixed head
 *    count, no title/regex matching to find "the quiz stage" or "the
 *    pair-work stage" (an earlier version did `/kviz/i` / `/juft/i` on
 *    `stage.title`, which silently dropped/misidentified stages on any
 *    deck that wasn't exactly 7 stages in Uzbek — e.g. a `ru`-language
 *    deck's "Квиз"/"Парная" titles never matched, corrupting the pager).
 *    The stage whose content IS the quiz just renders as a normal
 *    StageSlide (its `teacher_action`/`points`/`screen_text` describe
 *    running the quiz — real facilitation info, not a duplicate of the
 *    questions themselves).
 *  - `deck.quiz[]` and `deck.answer_key[]` are independent, already-generic
 *    arrays — no stage lookup needed to render them.
 *  - This trades the template's mid-flow quiz interleaving (quiz slides
 *    sitting between the "Tahlil" and "Juftlikda ish" stages) for a simple,
 *    robust two-part structure (all stages, then the whole assessment
 *    block) that can never lose or duplicate content regardless of how
 *    many stages/questions a real generation run produces.
 */
export function assembleSlides(deck: TeacherDeck): SlideEntry[] {
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

  const sortedStages = [...deck.stages].sort((a, b) => a.index - b.index);
  for (const stage of sortedStages) {
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

  if (deck.answer_key.length > 0) {
    slides.push({ key: "answer_key", label: "Kalit", node: <AnswerKeySlide items={deck.answer_key} /> });
  }

  slides.push({ key: "rubric", label: "Baholash", node: <RubricSlide rubric={deck.rubric} /> });

  return slides;
}

/**
 * Print-only rendering of the FULL slide list (used by `window.print()`).
 * The interactive `DeckPager` below only ever mounts the active slide, so
 * printing it as-is would yield a one-page PDF — this sibling container
 * renders every assembled slide, in order, each wrapped `.deck-slide` so
 * the `@media print` rules in `globals.css` can break one slide per page.
 * Hidden on screen (`.deck-print-view` is `display:none` by default) and
 * flipped visible only under `@media print`.
 */
function DeckPrintView({ slides }: { slides: SlideEntry[] }) {
  return (
    <div className="deck-print-view">
      {slides.map((s) => (
        <div key={s.key} className="deck-slide">
          {s.node}
        </div>
      ))}
    </div>
  );
}

function DeckPager({ slides }: { slides: SlideEntry[] }) {
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
    <div className="print-hide mt-7 space-y-5">
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

  // Slide list is assembled once here (not inside DeckPager) so both the
  // interactive pager and the print-only all-slides view render from the
  // exact same array.
  const slides = useMemo(() => (deck ? assembleSlides(deck) : []), [deck]);

  // Chrome/Safari derive the print/PDF suggested filename from
  // `document.title`. Once the deck has loaded, rename the tab to
  // grade + topic number + topic title so "Download PDF" saves under a
  // meaningful name instead of the app's generic title. Restored on
  // unmount/deck-change so navigating away doesn't leave the tab titled
  // after a lesson.
  useEffect(() => {
    if (!deck) return;
    const previousTitle = document.title;
    const title = deckPdfTitle(deck.meta);
    if (title) document.title = title;
    return () => {
      document.title = previousTitle;
    };
  }, [deck]);

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
      <div className="print-hide">
        <SpaceBackdrop />
      </div>

      <div className="relative z-10">
        <div className="print-hide flex items-center justify-between gap-3">
          <Link to={`/job/${id}`} className={BACK_PILL}>
            <ArrowLeft className="size-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
            Back to job
          </Link>

          <motion.button
            type="button"
            onClick={() => window.print()}
            whileTap={{ scale: 0.96 }}
            className={cn(GLASS_BTN, "px-3.5 py-2 text-xs")}
          >
            <Download className="size-3.5" />
            Download PDF
          </motion.button>
        </div>

        <h1 className="print-hide mt-6 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Teacher deck
        </h1>
        <p className="print-hide mt-2 flex flex-wrap items-center gap-x-2 font-mono text-[0.72rem] uppercase tracking-[0.16em] text-white/50">
          <span>{deck.meta.subject_label}</span>
          <span className="text-white/25">·</span>
          <span>{deck.meta.grade}-sinf</span>
          <span className="text-white/25">·</span>
          <span>{deck.meta.topic_number}-mavzu</span>
        </p>

        <DeckPager slides={slides} />
      </div>

      <DeckPrintView slides={slides} />
    </div>
  );
}
