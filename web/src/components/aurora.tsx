import { motion } from "motion/react";

/**
 * Animated mesh-gradient aurora — three blurred orbs slowly drifting over a
 * conic chromatic background. Honors prefers-reduced-motion.
 */
export function Aurora() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0 overflow-hidden motion-reduce:hidden"
    >
      {/* Slow rotating conic backdrop */}
      <div className="absolute inset-[-40%] animate-[spin_28s_linear_infinite] [background:conic-gradient(from_var(--grad-angle),oklch(0.79_0.13_70_/_12%),oklch(0.66_0.16_35_/_8%),oklch(0.62_0.16_270_/_10%),oklch(0.72_0.10_195_/_6%),oklch(0.79_0.13_70_/_12%))] blur-[80px] opacity-90" />

      {/* Orbs */}
      <motion.div
        className="absolute size-[60vw] max-w-[720px] aspect-square rounded-full blur-[70px] mix-blend-screen opacity-60"
        style={{
          top: "-8%",
          left: "-8%",
          background:
            "radial-gradient(circle, var(--color-amber) 0%, transparent 65%)",
        }}
        animate={{ x: ["0vw", "8vw"], y: ["0vh", "6vh"], scale: [1, 1.12] }}
        transition={{
          duration: 22,
          repeat: Number.POSITIVE_INFINITY,
          repeatType: "reverse",
          ease: "easeInOut",
        }}
      />
      <motion.div
        className="absolute size-[50vw] max-w-[600px] aspect-square rounded-full blur-[70px] mix-blend-screen opacity-55"
        style={{
          bottom: "-12%",
          right: "-8%",
          background: "radial-gradient(circle, var(--color-rust) 0%, transparent 65%)",
        }}
        animate={{ x: ["0vw", "-6vw"], y: ["0vh", "-4vh"], scale: [1, 1.08] }}
        transition={{
          duration: 28,
          repeat: Number.POSITIVE_INFINITY,
          repeatType: "reverse",
          ease: "easeInOut",
        }}
      />
      <motion.div
        className="absolute size-[40vw] max-w-[460px] aspect-square rounded-full blur-[70px] mix-blend-screen opacity-40"
        style={{
          top: "30%",
          left: "55%",
          background:
            "radial-gradient(circle, var(--color-indigo) 0%, transparent 65%)",
        }}
        animate={{ x: ["0vw", "-10vw"], y: ["0vh", "4vh"], scale: [0.9, 1.15] }}
        transition={{
          duration: 32,
          repeat: Number.POSITIVE_INFINITY,
          repeatType: "reverse",
          ease: "easeInOut",
        }}
      />

      {/* Subtle grain overlay */}
      <div className="absolute inset-0 opacity-50 mix-blend-overlay [background-image:radial-gradient(oklch(0.99_0.005_80_/_1.5%)_1px,transparent_1px),radial-gradient(oklch(0.99_0.005_80_/_1%)_1px,transparent_1px)] [background-size:3px_3px,7px_7px] [background-position:0_0,1.5px_1.5px]" />
    </div>
  );
}
