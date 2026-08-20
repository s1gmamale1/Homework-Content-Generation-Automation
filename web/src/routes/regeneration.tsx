import { CampaignList } from "@/components/regeneration/campaign-list";
import { CampaignReport } from "@/components/regeneration/campaign-report";
import { type CanaryPacket, CanaryReview } from "@/components/regeneration/canary-review";
import { RegenerationWizard } from "@/components/regeneration/regeneration-wizard";
import { SpaceBackdrop } from "@/components/space-backdrop";
import {
  type Campaign,
  type PlanTarget,
  TASK_10_HINT,
  type TargetOutcome,
  type WizardState,
  defaultWizardState,
} from "@/lib/regeneration-state";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * Regeneration area (Task 4 shell) — a dedicated workspace, deliberately NOT
 * the Fleet batch launcher, reached only when `VITE_REGENERATION_ENABLED=1`.
 *
 * EVERYTHING on this page is fixture data declared below. There is no query
 * client, no API import and no backend call: creating or estimating a campaign
 * here spends nothing and publishes nothing. Task 10 replaces these constants
 * with typed API responses; because they all live in this one file, that swap
 * touches one module and leaves the components untouched.
 *
 * The phase closures below are the PLANNER's expansion of `PHASE_DEPS`, carried
 * as data. The UI never recomputes the dependency graph in TypeScript.
 */
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

/* ── Fixtures (Task 10 replaces this whole block) ────────────────────── */

/** The 11 content phases a job produces, in flow order. */
const CONTENT_PHASES = [
  "case-based-preview",
  "flashcards",
  "memory-check",
  "practice-rlc",
  "practice-error-detection",
  "practice-memory-match",
  "practice-tictactoe",
  "practice-jigsaw",
  "practice-sentence",
  "boss-arena",
  "reflection",
];

/** Planner-supplied downstream closure per phase, inclusive of the phase. */
const PHASE_CLOSURES: Record<string, string[]> = {
  "case-based-preview": [
    "case-based-preview",
    "practice-rlc",
    "practice-error-detection",
    "practice-tictactoe",
    "practice-jigsaw",
    "practice-sentence",
    "boss-arena",
    "reflection",
  ],
  flashcards: [
    "flashcards",
    "memory-check",
    "practice-rlc",
    "practice-error-detection",
    "practice-memory-match",
    "practice-tictactoe",
    "practice-jigsaw",
    "practice-sentence",
    "boss-arena",
    "reflection",
  ],
  "memory-check": [
    "memory-check",
    "practice-error-detection",
    "practice-memory-match",
    "boss-arena",
    "reflection",
  ],
  "practice-rlc": ["practice-rlc"],
  "practice-error-detection": ["practice-error-detection"],
  "practice-memory-match": ["practice-memory-match"],
  "practice-tictactoe": ["practice-tictactoe"],
  "practice-jigsaw": ["practice-jigsaw"],
  "practice-sentence": ["practice-sentence"],
  "boss-arena": ["boss-arena", "reflection"],
  reflection: ["reflection"],
};

const FIXTURE_LESSONS: PlanTarget[] = [
  {
    lessonId: "l-1",
    lessonTitle: "1-mavzu. Hujayra tuzilishi",
    language: "uz",
    sourceVersion: 1,
    nextVersion: 2,
  },
  {
    lessonId: "l-2",
    lessonTitle: "2-mavzu. Fotosintez",
    language: "uz",
    sourceVersion: 1,
    nextVersion: 2,
  },
  {
    lessonId: "l-3",
    lessonTitle: "3-mavzu. Nafas olish",
    language: "uz",
    sourceVersion: 2,
    nextVersion: 3,
  },
  {
    lessonId: "l-4",
    lessonTitle: "Тема 1. Строение клетки",
    language: "ru",
    sourceVersion: 1,
    nextVersion: 2,
  },
  {
    lessonId: "l-5",
    lessonTitle: "Тема 2. Фотосинтез",
    language: "ru",
    sourceVersion: 2,
    nextVersion: 3,
  },
];

const FIXTURE_CAMPAIGNS: Campaign[] = [
  {
    id: "c-uz-flashcards",
    name: "Biology 8 · UZ · flashcard prompt refresh",
    status: "awaiting_approval",
    targets: FIXTURE_LESSONS.filter((l) => l.language === "uz"),
    canarySize: 1,
    createdAt: "2026-08-20T09:00:00Z",
    estimate: { costLowUsd: 1.2, costHighUsd: 3.0, expectedModelCalls: 60 },
  },
  {
    id: "c-ru-single",
    name: "Biology 8 · RU · single-lesson fix",
    status: "awaiting_approval",
    targets: [FIXTURE_LESSONS[4]],
    canarySize: 1,
    createdAt: "2026-08-19T14:30:00Z",
    estimate: { costLowUsd: 0.2, costHighUsd: 0.5, expectedModelCalls: 10 },
  },
  {
    id: "c-uz-reflection",
    name: "Biology 8 · UZ · reflection rewrite",
    status: "completed",
    targets: FIXTURE_LESSONS.filter((l) => l.language === "uz"),
    canarySize: 2,
    createdAt: "2026-08-14T08:15:00Z",
    estimate: { costLowUsd: 0.24, costHighUsd: 0.6, expectedModelCalls: 12 },
  },
];

const FIXTURE_CANARY: Record<string, CanaryPacket> = {
  "c-uz-flashcards": {
    lessonTitle: "1-mavzu. Hujayra tuzilishi",
    language: "uz",
    sourceVersion: 1,
    nextVersion: 2,
    latencySeconds: 214,
    estimatedCostUsd: 0.35,
    actualCostUsd: 0.42,
    phases: [
      {
        provenance: { phase: "case-based-preview", origin: "copied", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "Hujayra — tirik organizmning eng kichik tuzilmaviy birligi…",
      },
      {
        provenance: { phase: "flashcards", origin: "regenerated", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "24 ta kartochka: sitoplazma, yadro, mitoxondriya, ribosoma…",
      },
      {
        provenance: { phase: "memory-check", origin: "regenerated", sourceVersion: 1 },
        judge: "major_shipped",
        warnings: ["lint:coverage_thin"],
        excerpt: "10 ta savol, har biri kartochkalardagi atamaga bog'langan…",
      },
      {
        provenance: { phase: "practice-rlc", origin: "regenerated", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "Real hayotdan uchta vaziyat: shifokor, dehqon, oshpaz…",
      },
      {
        provenance: {
          phase: "practice-error-detection",
          origin: "regenerated",
          sourceVersion: 1,
        },
        judge: "pass",
        warnings: [],
        excerpt: "Beshta xatoli jumla — har birida bitta atama noto'g'ri ishlatilgan…",
      },
      {
        provenance: { phase: "practice-memory-match", origin: "regenerated", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "12 juft karta: organoid ↔ vazifasi…",
      },
      {
        provenance: { phase: "practice-tictactoe", origin: "regenerated", sourceVersion: 1 },
        judge: "major_regen_failed",
        warnings: ["judge: two cells repeat the same question"],
        excerpt: "3×3 katak, har katakda bitta savol…",
      },
      {
        provenance: { phase: "practice-jigsaw", origin: "regenerated", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "Hujayra bo'linishi bosqichlarini to'g'ri tartibda joylashtiring…",
      },
      {
        provenance: { phase: "practice-sentence", origin: "regenerated", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "Berilgan so'zlardan to'liq jumla tuzing…",
      },
      {
        provenance: { phase: "boss-arena", origin: "regenerated", sourceVersion: 1 },
        judge: "unavailable",
        warnings: ["judge transport error"],
        excerpt: "Boss jangi: 5 bosqich, har birida HP va vaqt chegarasi…",
      },
      {
        provenance: { phase: "reflection", origin: "regenerated", sourceVersion: 1 },
        judge: "pass",
        warnings: [],
        excerpt: "Bugun nimani o'rgandingiz? Uchta jumlada yozing…",
      },
    ],
  },
  "c-ru-single": {
    lessonTitle: "Тема 2. Фотосинтез",
    language: "ru",
    sourceVersion: 2,
    nextVersion: 3,
    latencySeconds: 96,
    estimatedCostUsd: 0.18,
    actualCostUsd: 0.18,
    phases: [
      {
        provenance: { phase: "boss-arena", origin: "copied", sourceVersion: 2 },
        judge: "pass",
        warnings: [],
        excerpt: "Битва с боссом: пять этапов по теме фотосинтеза…",
      },
      {
        provenance: { phase: "reflection", origin: "regenerated", sourceVersion: 2 },
        judge: "refused",
        warnings: ["judge declined: safety filter"],
        excerpt: "Что нового вы узнали о световой фазе? Ответьте тремя предложениями…",
      },
    ],
  },
};

const FIXTURE_OUTCOMES: Record<string, TargetOutcome[]> = {
  "c-uz-reflection": [
    {
      lessonId: "l-1",
      lessonTitle: "1-mavzu. Hujayra tuzilishi",
      language: "uz",
      status: "published",
      publishedVersion: 2,
      reasonCode: null,
    },
    {
      lessonId: "l-2",
      lessonTitle: "2-mavzu. Fotosintez",
      language: "uz",
      status: "publication_pending",
      publishedVersion: null,
      reasonCode: "publication_queued",
    },
    {
      lessonId: "l-3",
      lessonTitle: "3-mavzu. Nafas olish",
      language: "uz",
      status: "publication_failed",
      publishedVersion: null,
      reasonCode: "notion_parent_missing",
    },
    {
      lessonId: "l-4",
      lessonTitle: "Тема 1. Строение клетки",
      language: "ru",
      status: "generation_failed",
      publishedVersion: null,
      reasonCode: "provider_quota_exhausted",
    },
    {
      lessonId: "l-5",
      lessonTitle: "Тема 2. Фотосинтез",
      language: "ru",
      status: "abandoned",
      publishedVersion: null,
      reasonCode: "operator_abandoned",
    },
  ],
};

/* ── Page ────────────────────────────────────────────────────────────── */

export function RegenerationPage() {
  const [wizard, setWizard] = useState<WizardState>(defaultWizardState);
  const [selectedId, setSelectedId] = useState<string>(FIXTURE_CAMPAIGNS[0].id);

  const selected = FIXTURE_CAMPAIGNS.find((c) => c.id === selectedId) ?? null;
  const packet = selected ? (FIXTURE_CANARY[selected.id] ?? null) : null;
  const outcomes = selected ? (FIXTURE_OUTCOMES[selected.id] ?? []) : [];

  const inert = () => toast(TASK_10_HINT);

  return (
    <div className="relative">
      <SpaceBackdrop />

      <div className="relative z-10 space-y-7">
        <header className="flex items-start gap-4">
          <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30 shadow-[0_18px_40px_-18px_rgba(124,92,255,0.8)]">
            <RefreshCw className="size-7 text-white" />
          </span>
          <div>
            <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-[2.75rem]">
              Regeneration
            </h1>
            <p className="mt-2 max-w-[62ch] text-sm leading-6 text-white/55">
              Rebuild phases of homework that is already published, review a canary packet, and
              release a new version. This workspace is separate from the Fleet launcher on purpose —
              nothing here starts a first-run batch.
            </p>
          </div>
        </header>

        <div className={cn(CARD, "text-xs leading-5 text-white/45")}>
          Shell preview: every campaign, lesson and packet below is fixture data. Buttons that would
          change state are inert — {TASK_10_HINT}
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="space-y-4">
            <h2 className="text-sm font-semibold text-white/70">New campaign</h2>
            <RegenerationWizard
              lessons={FIXTURE_LESSONS}
              allPhases={CONTENT_PHASES}
              phaseClosures={PHASE_CLOSURES}
              state={wizard}
              onChange={setWizard}
              onLaunch={inert}
            />
          </div>

          <div className="space-y-4">
            <CampaignList
              campaigns={FIXTURE_CAMPAIGNS}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
            {selected && packet && (
              <CanaryReview
                campaign={selected}
                packet={packet}
                onApprove={inert}
                onReject={inert}
              />
            )}
            {selected && outcomes.length > 0 && <CampaignReport outcomes={outcomes} />}
            {selected && !packet && outcomes.length === 0 && (
              <p className={cn(CARD, "text-xs text-white/40")}>
                This campaign has no canary packet and no outcomes yet.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
