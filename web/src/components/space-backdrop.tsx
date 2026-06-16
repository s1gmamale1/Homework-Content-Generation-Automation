/**
 * Sigma living-glass backdrop, mounted per-route as the first child of a
 * `relative` page wrapper (content sits in a sibling with `relative z-10`).
 * Used by all 10 routes. Renders three fixed, non-interactive layers: a deep
 * navy base, a slowly drifting aurora bloom (the `.aurora-bloom` CSS class —
 * its drift is frozen under prefers-reduced-motion via globals.css), and a
 * faint starfield. NOTE: keep this mounted per-route — hoisting it into
 * layout.tsx creates a containing block that breaks the fixed positioning.
 */
export function SpaceBackdrop() {
  return (
    <>
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            "linear-gradient(160deg, oklch(0.165 0.022 275), oklch(0.12 0.016 268) 55%, oklch(0.15 0.02 250))",
        }}
      />
      <div aria-hidden className="aurora-bloom" />
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0 opacity-70"
        style={{
          backgroundImage:
            "radial-gradient(1.5px 1.5px at 30px 40px, rgba(255,255,255,0.35), transparent), radial-gradient(1px 1px at 130px 120px, rgba(255,255,255,0.22), transparent), radial-gradient(1px 1px at 220px 70px, rgba(255,255,255,0.18), transparent), radial-gradient(1.5px 1.5px at 300px 200px, rgba(255,255,255,0.28), transparent)",
          backgroundSize: "260px 260px, 200px 200px, 340px 340px, 420px 420px",
        }}
      />
    </>
  );
}
