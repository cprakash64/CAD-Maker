"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function Mark() {
  // Compact "precision part" glyph — engineering, not a generic spark. Brass.
  return (
    <span className="grid h-7 w-7 place-items-center rounded-md border border-edge bg-raised">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
        <rect x="1.5" y="1.5" width="13" height="13" rx="2" stroke="#c2974a" strokeWidth="1.3" />
        <circle cx="8" cy="8" r="2.4" stroke="#9aa1ab" strokeWidth="1.2" />
        <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" stroke="#9aa1ab" strokeWidth="1.2" />
      </svg>
    </span>
  );
}

function SolverStatus() {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    api.health()
      .then((h) => setLabel(h.llm_provider ?? null))
      .catch(() => setLabel(null));
  }, []);
  if (!label) return null;
  return (
    <span
      className="hidden items-center gap-1.5 rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-slate-400 md:inline-flex"
      title="Active generation provider"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-accent" />
      Solver: <span className="text-slate-200">{label}</span>
    </span>
  );
}

export function Header() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="sticky top-0 z-30 border-b border-edge bg-ink/85 backdrop-blur">
      <div className="flex items-center justify-between px-4 py-2">
        <Link href={user ? "/dashboard" : "/"} className="flex items-center gap-2.5">
          <Mark />
          <span className="text-sm font-semibold tracking-tight text-slate-100">
            CAD&nbsp;Maker
          </span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {user ? (
            <>
              <SolverStatus />
              <Link href="/dashboard" className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised">
                Designs
              </Link>
              <Link href="/drawing" className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised">
                Drawing → CAD
              </Link>
              <Link href="/docs/import" className="hidden rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised sm:block">
                Docs
              </Link>
              <Link href="/designs/new" className="btn-primary btn-sm ml-1">
                + New part
              </Link>
              <span className="ml-2 hidden border-l border-edge pl-3 text-xs text-slate-500 lg:inline">
                {user.email}
              </span>
              <button
                className="rounded-md px-2.5 py-1.5 text-xs text-slate-400 hover:bg-raised hover:text-slate-200"
                onClick={() => {
                  logout();
                  router.push("/");
                }}
              >
                Sign out
              </button>
            </>
          ) : (
            <>
              <Link href="/signin" className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised">
                Sign in
              </Link>
              <Link href="/signup" className="btn-primary btn-sm">
                Get started
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
