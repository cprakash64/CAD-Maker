"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function AuthForm({ mode }: { mode: "signin" | "signup" }) {
  const router = useRouter();
  const { login, signup } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (isSignup) await signup(email, password);
      else await login(email, password);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm space-y-5 py-10">
      <h1 className="text-2xl font-bold">
        {isSignup ? "Create your account" : "Sign in"}
      </h1>
      <form onSubmit={submit} className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-xs text-slate-400">Email</span>
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
          <span className="mb-1 block text-xs text-slate-400">
            Password{isSignup ? " (min 8 characters)" : ""}
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
        {error && (
          <div className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-200">
            {error}
          </div>
        )}
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "…" : isSignup ? "Sign up" : "Sign in"}
        </button>
      </form>
      <p className="text-sm text-slate-400">
        {isSignup ? (
          <>
            Already have an account?{" "}
            <Link href="/signin" className="text-accent underline">
              Sign in
            </Link>
          </>
        ) : (
          <>
            New here?{" "}
            <Link href="/signup" className="text-accent underline">
              Create an account
            </Link>
          </>
        )}
      </p>
    </div>
  );
}
