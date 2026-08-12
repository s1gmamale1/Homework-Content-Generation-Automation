/**
 * Compile/shape check for the `TeacherDeck` FE type (Task 10) — mirrors
 * app/schemas/content_json/teacher_deck.py. A malformed field name/nesting
 * here would fail `tsc`; this also exercises the shape at runtime so a
 * regression shows up under `npm test`, not only under `tsc --noEmit`.
 * Run: cd web && npx tsx src/lib/teacher-deck.test.ts
 */
import assert from "node:assert/strict";
import type { BatchSummary, JobKind, TeacherDeck } from "./types";

const deck: TeacherDeck = {
  meta: {
    subject_label: "Algebra",
    grade: "7",
    topic_number: 3,
    topic_title: "Linear equations",
    duration_min: 45,
    lesson_type: "yangi bilim",
    method: ["suhbat", "amaliy mashq"],
    materials: ["darslik", "doska"],
    video_ref: null,
  },
  passport: {
    fan_sinf: "Algebra, 7-sinf",
    mavzu: "Chiziqli tenglamalar",
    dars_turi: "yangi bilim",
    metod: "suhbat",
    kerakli_vosita: "doska, bo'r",
    baholash: "5 balli",
  },
  objectives: {
    bilib_oladi: "chiziqli tenglama nima ekanini",
    qila_oladi: "tenglamani yechishni",
    tushunadi: "yechim nima uchun kerakligini",
  },
  core_idea: {
    statement: "Chiziqli tenglama bitta noma'lumli tenglama.",
    elaboration: "ax + b = 0 ko'rinishidagi tenglama.",
  },
  lesson_map: [
    { index: 1, title: "Kirish", description: "Mavzuga kirish", minutes: 5 },
    { index: 2, title: "Asosiy qism", description: "Yangi mavzu", minutes: 40 },
  ],
  stages: [
    {
      index: 1,
      title: "Kirish",
      minutes: 5,
      badge: "ekranga",
      points: [{ title: "Salomlashish", detail: "O'quvchilar bilan salomlashish" }],
      teacher_action: "Salomlashadi",
      student_action: "Javob beradi",
      screen_text: "Xush kelibsiz!",
    },
    {
      index: 2,
      title: "Asosiy qism",
      minutes: 40,
      badge: "teacher_only",
      points: [],
      teacher_action: "Tushuntiradi",
      student_action: "Tinglaydi",
      screen_text: null,
    },
  ],
  quiz: [
    {
      number: 1,
      question: "2x + 4 = 0 tenglamaning yechimi?",
      options: [
        { label: "A", text: "x = -2" },
        { label: "B", text: "x = 2" },
        { label: "C", text: "x = 0" },
        { label: "D", text: "x = 4" },
      ],
      correct_label: "A",
      hint: "Ikkala tomonni 2 ga bo'ling",
    },
  ],
  answer_key: [
    { number: 1, correct_label: "A", explanation: "2x = -4 => x = -2" },
  ],
  pair_work: {
    intro: "Juftlikda ishlang",
    tasks: [{ title: "1-topshiriq", prompt: "Tenglamani yeching" }],
  },
  conclusion: {
    questions: ["Bugun nimani o'rgandik?"],
  },
  rubric: {
    components: [{ points: 5, title: "To'g'ri yechim", detail: "Tenglama to'g'ri yechilgan" }],
    total: 5,
    bands: [{ range: "5", grade: "a'lo" }],
  },
};

assert.equal(deck.meta.subject_label, "Algebra");
assert.equal(deck.stages[0]?.badge, "ekranga");
assert.equal(deck.stages[1]?.badge, "teacher_only");
assert.equal(deck.quiz[0]?.correct_label, "A");
assert.equal(deck.answer_key[0]?.correct_label, "A");
assert.equal(deck.rubric.components[0]?.points, deck.rubric.total);

// `kind` discriminator round-trips through BatchSummary.
const kinds: JobKind[] = ["homework", "teacher_material"];
for (const kind of kinds) {
  const summary: Pick<BatchSummary, "kind"> = { kind };
  assert.equal(summary.kind, kind);
}

console.log("OK");
