import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { isAuthenticated, onAuthChange } from "@/lib/auth";

/**
 * Wraps protected routes. If no token is in sessionStorage, redirects to
 * /login (preserving the target path so we can bounce back after login).
 *
 * Re-checks on token-change events (sign-in, sign-out, 401 from any API
 * call). Same-origin tab sign-out is propagated via the storage event.
 */
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const [authed, setAuthed] = useState(() => isAuthenticated());

  useEffect(() => {
    return onAuthChange(() => setAuthed(isAuthenticated()));
  }, []);

  if (!authed) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    );
  }
  return <>{children}</>;
}
