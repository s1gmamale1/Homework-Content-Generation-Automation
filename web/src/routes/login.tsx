import { KeyRound, LogIn } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { Input } from "@/components/ui/input";
import { setToken } from "@/lib/auth";
import { tapScale } from "@/lib/motion";
import { CARD, INPUT_GLASS, PRIMARY_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

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
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10 mx-auto mt-20 flex max-w-md flex-col gap-6">
        <div>
          <span className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.16em] text-white/45">
            Authentication
          </span>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            Sign in with token
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-white/55">
            Paste the access token issued by your platform team. The token is
            stored only in this browser session — it disappears when you close
            the tab.
          </p>
        </div>

        <form onSubmit={onSubmit} className={cn(CARD, "flex flex-col gap-3")}>
          <label
            htmlFor="auth-token"
            className="font-mono text-[0.66rem] uppercase tracking-[0.16em] text-white/45"
          >
            <KeyRound className="mr-1.5 inline-block size-3" />
            Access token
          </label>
          <Input
            id="auth-token"
            type="password"
            // biome-ignore lint/a11y/noAutofocus: token field should focus on the login screen
            autoFocus
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => {
              setTokenInput(e.target.value);
              if (error) setError(null);
            }}
            placeholder="paste your token"
            className={cn(INPUT_GLASS, "font-mono")}
          />
          {error && (
            <p className="text-[0.7rem] text-rose-300" role="alert">
              {error}
            </p>
          )}
          <motion.button
            type="submit"
            disabled={!token.trim()}
            whileTap={tapScale}
            className={cn(PRIMARY_BTN, "mt-1 self-start")}
          >
            <LogIn className="size-3.5" />
            Continue
          </motion.button>
        </form>
      </div>
    </div>
  );
}
