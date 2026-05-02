import { KeyRound, LogIn } from "lucide-react";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Eyebrow } from "@/components/eyebrow";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { setToken } from "@/lib/auth";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [token, setTokenInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Where to send the user after a successful login. The route guard
  // stashes the original target in `location.state.from`; default to root.
  const from =
    (location.state as { from?: string } | null)?.from?.toString() ?? "/";

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) {
      setError("Token is required.");
      return;
    }
    setToken(trimmed);
    setError(null);
    navigate(from, { replace: true });
  }

  return (
    <div className="mx-auto mt-20 flex max-w-md flex-col gap-6">
      <div>
        <Eyebrow>Authentication</Eyebrow>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
          Sign in with token
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-(--color-ink-soft)">
          Paste the access token issued by your platform team. The token is
          stored only in this browser session — it disappears when you close
          the tab.
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-5"
      >
        <label
          htmlFor="auth-token"
          className="font-mono text-[0.66rem] uppercase tracking-[0.16em] text-(--color-ink-muted)"
        >
          <KeyRound className="mr-1.5 inline-block size-3" />
          Access token
        </label>
        <Input
          id="auth-token"
          type="password"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          value={token}
          onChange={(e) => {
            setTokenInput(e.target.value);
            if (error) setError(null);
          }}
          placeholder="paste your token"
          className="font-mono"
        />
        {error && (
          <p className="text-[0.7rem] text-(--color-error)" role="alert">
            {error}
          </p>
        )}
        <Button type="submit" disabled={!token.trim()} className="mt-1 self-start">
          <LogIn className="size-3.5" />
          Continue
        </Button>
      </form>
    </div>
  );
}
