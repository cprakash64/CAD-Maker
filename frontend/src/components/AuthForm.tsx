"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function AuthForm({ mode }: { mode: "signin" | "signup" }) {
  const router = useRouter();
  const params = useSearchParams();
  const { login, signup } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";
  // A prompt carried from the homepage gate — preserve it through auth so the
  // user lands in the new-design flow with their spec intact.
  const carriedPrompt = params.get("prompt");
  const qs = carriedPrompt ? `?prompt=${encodeURIComponent(carriedPrompt)}` : "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (isSignup) await signup(email, password);
      else await login(email, password);
      router.push(carriedPrompt ? `/new${qs}` : "/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm px-5 py-16 sm:py-20">
      <div className="mb-5 flex items-center gap-2.5">
        <span className="grid h-8 w-8 place-items-center rounded-lg border border-edge bg-raised/70">
          <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden>
            <rect x="1.5" y="1.5" width="13" height="13" rx="2.5" stroke="#d6aa4d" strokeWidth="1.3" />
            <circle cx="8" cy="8" r="2.4" stroke="#afa799" strokeWidth="1.2" />
            <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" stroke="#afa799" strokeWidth="1.2" />
          </svg>
        </span>
        <span className="text-sm font-semibold tracking-tight text-slate-100">LunaiCAD</span>
      </div>
      <div className="card p-6 shadow-lift">
        <h1 className="text-xl font-semibold tracking-tight text-slate-50">
          {isSignup ? "Create your account" : "Sign in"}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {carriedPrompt
            ? "Sign in to generate this part and export STEP/STL."
            : isSignup
              ? "Start generating validated parametric CAD."
              : "Welcome back to your part studio."}
        </p>
        {carriedPrompt && (
          <div className="mt-3 rounded-lg border border-edge bg-raised/40 p-3">
            <span className="label mb-1 block text-slate-500">Your spec</span>
            <p className="line-clamp-2 text-xs leading-relaxed text-slate-300">
              {carriedPrompt}
            </p>
          </div>
        )}
        <form onSubmit={submit} className="mt-5 space-y-3">
          <label className="block">
            <span className="label mb-1 block">Email</span>
            <input
              type="email"
              required
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <label className="block">
            <span className="label mb-1 block">
              Password{isSignup ? " · min 8 characters" : ""}
            </span>
            <input
              type="password"
              required
              minLength={isSignup ? 8 : undefined}
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isSignup ? "new-password" : "current-password"}
            />
          </label>
          {error && <div className="banner-danger">{error}</div>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "…" : isSignup ? "Create account" : "Sign in"}
          </button>
        </form>
      </div>
      <p className="mt-4 text-center text-sm text-slate-400">
        {isSignup ? (
          <>
            Already have an account?{" "}
            <Link href={`/signin${qs}`} className="text-accent hover:underline">
              Sign in
            </Link>
          </>
        ) : (
          <>
            New here?{" "}
            <Link href={`/signup${qs}`} className="text-accent hover:underline">
              Create an account
            </Link>
          </>
        )}
      </p>
    </div>
  );
}
