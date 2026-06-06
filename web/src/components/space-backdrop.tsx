/**
 * Shared deep-space backdrop used by the dark "space dashboard" pages
 * (Library, Usage, Section). Renders two fixed, non-interactive layers:
 * a navy base with purple/blue aurora, and a faint starfield. Drop it as
 * the first child of a `relative` page wrapper and put page content in a
 * sibling with `relative z-10`.
 */
export function SpaceBackdrop() {
  return (
    <>
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            "radial-gradient(55% 45% at 0% 0%, oklch(0.50 0.20 290 / 0.35), transparent 60%), radial-gradient(45% 40% at 100% 14%, oklch(0.55 0.17 250 / 0.28), transparent 60%), radial-gradient(60% 50% at 50% 112%, oklch(0.46 0.16 285 / 0.24), transparent 66%), linear-gradient(160deg, oklch(0.165 0.022 275), oklch(0.12 0.016 268) 55%, oklch(0.15 0.02 250))",
        }}
      />
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
