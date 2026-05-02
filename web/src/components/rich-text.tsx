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

const INLINE_COMPONENTS = {
  // Strip the wrapping <p> so markdown content can sit inside an inline
  // context (button, span, table cell) without nesting block inside inline.
  p: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
};

const BLOCK_COMPONENTS = {
  p: ({ children }: { children?: React.ReactNode }) => (
    <p className="my-1 leading-relaxed">{children}</p>
  ),
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
