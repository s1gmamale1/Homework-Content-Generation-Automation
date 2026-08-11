/** Stage slides (bosqich 1–4 in the assembled pager): renders differently by
 *  `stage.badge`.
 *  - `teacher_only` (light, blue): numbered teaching points + the two-column
 *    "O'qituvchi nima qiladi / O'quvchi nima qiladi" boxes at the bottom.
 *  - `ekranga` (dark): the projected `screen_text`, split into a headline
 *    paragraph and an optional highlighted "hook" box (title + detail) —
 *    `screen_text` encodes the split as a blank line, e.g.
 *    "<headline>\n\n<hook title>\n<hook detail>" (see the "Motivatsiya"
 *    stage in the fixture).
 */
import { CircleHelp } from "lucide-react";
import type { TeacherDeckStage } from "@/lib/types";
import {
  type Accent,
  DarkSlide,
  LightSlide,
  NumberedPoint,
  SlideKicker,
  SlideTitle,
  TeacherStudentBoxes,
  TitleUnderline,
  ACCENT_HEX,
  stageKickerLabel,
} from "./shared";

function parseScreenText(text: string): { headline: string; box?: { title: string; detail: string } } {
  const [headline, ...rest] = text.split("\n\n");
  const boxRaw = rest.join("\n\n").trim();
  if (!boxRaw) return { headline: headline.trim() };
  const [title, ...detailLines] = boxRaw.split("\n");
  const detail = detailLines.join(" ").trim();
  return { headline: headline.trim(), box: detail ? { title: title.trim(), detail } : undefined };
}

export function StageSlide({ stage, accent }: { stage: TeacherDeckStage; accent: Accent }) {
  const kicker = stageKickerLabel(stage.index, stage.minutes);

  if (stage.badge === "ekranga") {
    const { headline, box } = parseScreenText(stage.screen_text ?? "");
    return (
      <DarkSlide accent={accent}>
        <SlideKicker label={kicker} accent={accent} badge="ekranga" dark />
        <SlideTitle dark>{stage.title}</SlideTitle>

        <p className="mt-6 whitespace-pre-line text-lg leading-relaxed text-white/85 sm:text-xl">
          {headline}
        </p>

        {box && (
          <div className="mt-8 flex items-start gap-4 rounded-xl bg-white/[0.05] p-5">
            <span
              className="grid size-8 shrink-0 place-items-center rounded-full text-white"
              style={{ background: ACCENT_HEX[accent] }}
            >
              <CircleHelp className="size-4" />
            </span>
            <div>
              <p className="font-semibold text-white">{box.title}</p>
              <p className="mt-1 text-sm leading-relaxed text-white/55">{box.detail}</p>
            </div>
          </div>
        )}
      </DarkSlide>
    );
  }

  // teacher_only
  return (
    <LightSlide accent={accent}>
      <SlideKicker label={kicker} accent={accent} badge="teacher_only" />
      <SlideTitle>{stage.title}</SlideTitle>
      <TitleUnderline accent={accent} />

      <div className="space-y-3">
        {stage.points.map((p, i) => (
          <NumberedPoint key={p.title} n={i + 1} title={p.title} detail={p.detail} accent={accent} />
        ))}
      </div>

      <TeacherStudentBoxes
        teacherAction={stage.teacher_action}
        studentAction={stage.student_action}
        accent={accent}
      />
    </LightSlide>
  );
}
