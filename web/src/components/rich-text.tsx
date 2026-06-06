import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { cn, stripCurriculumTags } from "@/lib/utils";

interface RichTextProps {
  children: string | null | undefined;
  className?: string;
  inline?: boolean;
  /** Skip the automatic curriculum-tag strip. Default false (strip on). */
  raw?: boolean;
}

/**
 * Render a `![visual: … ](placeholder)` sentinel as a described-visual card
 * instead of a broken <img>. The engine never emits real images or <svg> — every
 * visual is a placeholder whose alt text describes what to draw (see
 * `app/services/agent.py` `_PLACEHOLDER_RULES`).
 */
const PlaceholderImg = ({ src, alt }: { src?: string; alt?: string }) => {
  if (src !== "placeholder") {
    // Defensive: a real image URL (should not occur) renders normally.
    return <img src={src} alt={alt} />;
  }
  const desc = (alt ?? "").replace(/^\s*(visual|placeholder)\s*:\s*/i, "").trim();
  return (
    <span className="my-2 inline-flex items-start gap-2 rounded-(--radius-sm) border border-dashed border-amber-400/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-300/40 dark:bg-amber-950/40 dark:text-amber-200">
      <span aria-hidden>🖼️</span>
      <span>
        <span className="font-medium">Visual needed:</span> {desc || "image gen required"}
      </span>
    </span>
  );
};

const INLINE_COMPONENTS = {
  // Strip the wrapping <p> so markdown content can sit inside an inline
  // context (button, span, table cell) without nesting block inside inline.
  p: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  img: PlaceholderImg,
};

const BLOCK_COMPONENTS = {
  p: ({ children }: { children?: React.ReactNode }) => (
    <p className="my-1 leading-relaxed">{children}</p>
  ),
  img: PlaceholderImg,
};

/**
 * Render any model-generated string that may contain inline `<svg>`,
 * Markdown formatting, or both. Uses `rehype-raw` so raw HTML (especially
 * SVG) is preserved instead of being escaped to text.
 *
 * Use this anywhere a Pydantic-schema string field comes back from Gemini —
 * boss prompts, flashcard fronts/backs, sprint items, reading checkpoints,
 * game options. The model is told (via the universal SVG rules in
 * `app/services/gemini.py`) that white-bg, content-scaled SVGs are valid,
 * so we just need to actually render them when they show up.
 */
export function RichText({ children, className, inline = false, raw = false }: RichTextProps) {
  if (!children) return null;
  // Strip curriculum metadata tags (Bloom/PISA/Damage/Difficulty) by default.
  // They belong in schema fields (e.g., BossQuestion.bloom_level), not inline.
  // Older jobs and occasional model double-encoding leak them; this auto-fixes
  // them at render time. Pass `raw` to opt out.
  const text = raw ? children : stripCurriculumTags(children);
  if (!text) return null;
  return (
    <div
      className={cn(
        "rich-text [&_svg]:max-w-full [&_svg]:h-auto [&_svg]:rounded-(--radius-sm)",
        inline && "inline-block",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={inline ? INLINE_COMPONENTS : BLOCK_COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
