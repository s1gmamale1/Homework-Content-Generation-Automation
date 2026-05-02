// Token-based auth for the SPA. The token comes from one of two sources:
//   1. The user pastes it into the login form (manual flow).
//   2. Production: an upstream service injects the token into requests
//      directly (header/cookie); the SPA's stored copy is unused.
//
// We use sessionStorage (cleared on tab close) rather than localStorage
// so the token doesn't outlive the browser session. Bearer tokens are
// short-lived secrets — outliving the session is a security regression.

const TOKEN_KEY = "class-homework-builder-auth-token";
const AUTH_EVENT = "class-homework-builder-auth-changed";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
    window.dispatchEvent(new Event(AUTH_EVENT));
  } catch {
    // sessionStorage unavailable (private mode, quota exceeded). Silent —
    // a 401 from the next API call surfaces the error visibly.
  }
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event(AUTH_EVENT));
  } catch {
    // ignore
  }
}

export function isAuthenticated(): boolean {
  return Boolean(getToken());
}

/** Subscribe to token-change events. Returns an unsubscribe fn. */
export function onAuthChange(handler: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener(AUTH_EVENT, handler);
  // Also listen to native `storage` events so other tabs sign-out propagates.
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener(AUTH_EVENT, handler);
    window.removeEventListener("storage", handler);
  };
}
