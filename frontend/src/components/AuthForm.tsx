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
    <div className="mx-auto max-w-sm py-14">
      <div className="card p-6">
        <h1 className="text-xl font-semibold text-slate-50">
          {isSignup ? "Create your account" : "Sign in"}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {isSignup
            ? "Start generating validated parametric CAD."
            : "Welcome back to your part studio."}
        </p>
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
            <Link href="/signin" className="text-accent hover:underline">
              Sign in
            </Link>
          </>
        ) : (
          <>
            New here?{" "}
            <Link href="/signup" className="text-accent hover:underline">
              Create an account
            </Link>
          </>
        )}
      </p>
    </div>
  );
}
