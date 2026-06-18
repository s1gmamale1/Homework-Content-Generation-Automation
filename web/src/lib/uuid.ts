/**
 * RFC4122 v4 UUID that works in NON-secure contexts.
 *
 * `crypto.randomUUID()` exists in all modern browsers but ONLY in a secure
 * context (HTTPS or `localhost`). When the app is served over plain HTTP on a
 * LAN IP (e.g. a fleet head at `http://192.168.x.x:8000`), `crypto.randomUUID`
 * is `undefined` and calling it throws `TypeError: crypto.randomUUID is not a
 * function` — which silently broke the section-page Generate/Retry flow. This
 * helper prefers the native impl, falls back to `crypto.getRandomValues` (which
 * IS available in non-secure contexts), and finally to `Math.random`. The value
 * is only an idempotency key, so cryptographic strength is not required.
 */
export function safeUUID(): string {
  const c: Crypto | undefined = globalThis.crypto;

  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }

  if (c && typeof c.getRandomValues === "function") {
    const b = c.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // variant 10xx
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0"));
    return (
      `${h[0]}${h[1]}${h[2]}${h[3]}-${h[4]}${h[5]}-${h[6]}${h[7]}-` +
      `${h[8]}${h[9]}-${h[10]}${h[11]}${h[12]}${h[13]}${h[14]}${h[15]}`
    );
  }

  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
