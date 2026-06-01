import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import type { SourceMap } from "@/lib/types";

export function SourceMapView({ map }: { map: SourceMap }) {
  if (!map.concepts?.length) {
    return <p className="text-sm text-(--color-ink-muted)">No source map.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
        {map.subject_family} · {map.chapter} · {map.section}
      </p>
      <div className="flex flex-col gap-2">
        {map.concepts.map((c) => (
          <div
            key={c.id}
            className="rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-3"
          >
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <Badge variant="accent" size="sm">
                {c.id}
              </Badge>
              <span className="text-sm font-medium text-(--color-ink)">{c.label}</span>
              {c.kind && (
                <Badge variant="neutral" size="sm">
                  {c.kind}
                </Badge>
              )}
            </div>
            <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">
              {c.statement}
            </RichText>
            {c.source_ref && (
              <p className="mt-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                {c.source_ref}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
