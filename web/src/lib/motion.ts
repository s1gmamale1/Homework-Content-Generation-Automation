import type { Transition, Variants } from "motion/react";

/**
 * Shared Apple-style motion primitives. Used across the app so transitions
 * feel identical everywhere. Reduced-motion is handled globally by the
 * `<MotionConfig reducedMotion="user">` wrapper in App.tsx — it strips
 * transform/scale animations and keeps opacity for users who request it, so
 * these variants don't need per-component guards.
 */

/** Gentle spring with a soft settle — the default for page/element moves. */
export const springSoft: Transition = {
  type: "spring",
  stiffness: 320,
  damping: 32,
  mass: 0.7,
};

/** iOS-style button press. Apply as `whileTap={tapScale}`. */
export const tapScale = { scale: 0.97 };

/** Parent of a list/grid: reveals children one after another. */
export const staggerContainer: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05, delayChildren: 0.03 } },
};

/** A single item that fades and rises into place. */
export const fadeUpItem: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.34, ease: [0.22, 1, 0.36, 1] },
  },
};
