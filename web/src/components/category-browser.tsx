import { ArrowLeft, ChevronRight } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { fadeUpItem, staggerContainer } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * Generic category drill-down used by both the Library grid and the Fleet
 * Tray. Top level shows one tile per group that actually has cards; clicking a
 * tile reveals that group's cards with a "back" control. Purely presentational
 * — it receives already-loaded items and never fetches.
 *
 * The grouping key is arbitrary (subject, grade, …): the caller supplies how to
 * read it off an item and how to label / tint / badge each group.
 */
interface CategoryBrowserProps<T> {
  items: T[];
  /** How to read the grouping key off an item (e.g. the grade). */
  getGroupKey: (item: T) => string;
  /** Human label for a group key (e.g. "Grade 7"). */
  groupLabel: (key: string) => string;
  /** Draws the cards for one group. */
  renderItems: (items: T[], key: string) => React.ReactNode;
  /** Accent gradient `[from, to]` for a group's avatar. Defaults to slate. */
  groupAccent?: (key: string) => [string, string];
  /** Short avatar content for a group. Defaults to the label's first char. */
  groupBadge?: (key: string) => string;
  /** Per-tile / header count caption. Defaults to "N item(s)". */
  countLabel?: (items: T[], key: string) => string;
  /** Sort comparator over `[key, items]` groups. Defaults to count desc. */
  sortGroups?: (a: [string, T[]], b: [string, T[]]) => number;
  /** Text on the detail-view back button. Defaults to "Back". */
  backLabel?: string;
}

const SLATE: [string, string] = ["#8aa0c6", "#5f6f93"];

function defaultCount(items: unknown[]): string {
  return `${items.length} item${items.length === 1 ? "" : "s"}`;
}

export function CategoryBrowser<T>({
  items,
  getGroupKey,
  groupLabel,
  renderItems,
  groupAccent,
  groupBadge,
  countLabel = defaultCount,
  sortGroups,
  backLabel = "Back",
}: CategoryBrowserProps<T>) {
  const accentFor = groupAccent ?? (() => SLATE);
  const badgeFor = groupBadge ?? ((k: string) => groupLabel(k).charAt(0));

  const groups = useMemo(() => {
    const map = new Map<string, T[]>();
    for (const item of items) {
      const k = getGroupKey(item);
      const bucket = map.get(k);
      if (bucket) bucket.push(item);
      else map.set(k, [item]);
    }
    const entries = [...map.entries()];
    entries.sort(
      sortGroups ??
        (([, a], [, b]) => b.length - a.length),
    );
    return entries;
  }, [items, getGroupKey, sortGroups]);

  const [selected, setSelected] = useState<string | null>(null);

  // If the selected group no longer has any cards, fall back to the tile grid.
  const selectedGroup = groups.find(([k]) => k === selected);
  useEffect(() => {
    if (selected !== null && !selectedGroup) setSelected(null);
  }, [selected, selectedGroup]);

  return (
    <AnimatePresence mode="wait" initial={false}>
      {selected !== null && selectedGroup ? (
        <motion.div
          key={`detail-${selected}`}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
          className="space-y-4"
        >
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="group inline-flex items-center gap-1.5 rounded-xl border border-white/[0.1] bg-white/[0.05] px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/[0.1] hover:text-white"
            >
              <ArrowLeft className="size-4 transition-transform group-hover:-translate-x-0.5" />
              {backLabel}
            </button>
            <Avatar accent={accentFor(selected)} badge={badgeFor(selected)} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-white">
                {groupLabel(selected)}
              </div>
              <div className="text-xs text-white/45">
                {countLabel(selectedGroup[1], selected)}
              </div>
            </div>
          </div>
          {renderItems(selectedGroup[1], selected)}
        </motion.div>
      ) : (
        <motion.div
          key="tiles"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <motion.div
            className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
            variants={staggerContainer}
            initial="hidden"
            animate="show"
          >
            {groups.map(([key, group]) => (
              <CategoryTile
                key={key}
                label={groupLabel(key)}
                accent={accentFor(key)}
                badge={badgeFor(key)}
                caption={countLabel(group, key)}
                onClick={() => setSelected(key)}
              />
            ))}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** One category tile — accent avatar, label, count, chevron. */
function CategoryTile({
  label,
  accent,
  badge,
  caption,
  onClick,
}: {
  label: string;
  accent: [string, string];
  badge: string;
  caption: string;
  onClick: () => void;
}) {
  return (
    <motion.button
      type="button"
      variants={fadeUpItem}
      onClick={onClick}
      className="group flex items-center gap-3 rounded-2xl border border-white/[0.09] bg-white/[0.04] p-4 text-left shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-all hover:-translate-y-0.5 hover:border-white/[0.16] hover:bg-white/[0.06]"
    >
      <Avatar accent={accent} badge={badge} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-white">{label}</div>
        <div className="mt-0.5 text-xs text-white/45">{caption}</div>
      </div>
      <ChevronRight className="size-4 shrink-0 text-white/30 transition-all group-hover:translate-x-0.5 group-hover:text-white/60" />
    </motion.button>
  );
}

/** Accent-gradient avatar holding a short badge (grade number / first letter). */
function Avatar({ accent, badge }: { accent: [string, string]; badge: string }) {
  const [from, to] = accent;
  return (
    <span
      className={cn(
        "grid size-10 shrink-0 place-items-center rounded-xl text-sm font-bold text-[#16131f]",
      )}
      style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
    >
      {badge}
    </span>
  );
}

/* ── Grade grouping helpers (shared by Library + Fleet) ──────────────────── */

/** Stable gradients cycled by grade number so each grade reads distinctly. */
const GRADE_GRADIENTS: [string, string][] = [
  ["#64a8ff", "#4d8dff"],
  ["#57e4a5", "#34d399"],
  ["#c18cff", "#8268ff"],
  ["#f6d365", "#fda085"],
  ["#4ee8d5", "#43c6ac"],
  ["#ff9466", "#ff5f7f"],
  ["#7c8cff", "#5fb0ff"],
];

/** Grouping key for a book's grade; "" buckets the ungraded ones. */
export function gradeKey(grade?: string | null): string {
  return grade ?? "";
}

export function gradeLabel(key: string): string {
  return key ? `Grade ${key}` : "Ungraded";
}

export function gradeBadge(key: string): string {
  return key || "—";
}

export function gradeAccent(key: string): [string, string] {
  if (!key) return SLATE;
  const n = Number.parseInt(key, 10);
  if (Number.isNaN(n)) return SLATE;
  return GRADE_GRADIENTS[n % GRADE_GRADIENTS.length];
}

/** Numeric ascending by grade; ungraded ("") sinks to the bottom. */
export function compareGradeGroups<T>(
  a: [string, T[]],
  b: [string, T[]],
): number {
  const na = a[0] ? Number.parseInt(a[0], 10) : Number.POSITIVE_INFINITY;
  const nb = b[0] ? Number.parseInt(b[0], 10) : Number.POSITIVE_INFINITY;
  if (na !== nb) return na - nb;
  return a[0].localeCompare(b[0]);
}
