"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

function Mark() {
  // Compact "precision part" glyph — reads as engineering, not a generic spark.
  return (
    <span className="grid h-7 w-7 place-items-center rounded-md border border-edge bg-raised">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
        <rect x="1.5" y="1.5" width="13" height="13" rx="2" stroke="#3f7fe0" strokeWidth="1.3" />
        <circle cx="8" cy="8" r="2.4" stroke="#9fb0c9" strokeWidth="1.2" />
        <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" stroke="#9fb0c9" strokeWidth="1.2" />
      </svg>
    </span>
  );
}

export function Header() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="sticky top-0 z-20 border-b border-edge bg-ink/85 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-2.5">
        <Link href={user ? "/dashboard" : "/"} className="flex items-center gap-2.5">
          <Mark />
          <span className="text-sm font-semibold tracking-tight text-slate-100">
            SourceCAD
          </span>
          <span className="hidden text-[11px] uppercase tracking-wider text-slate-500 sm:inline">
            Part Studio
          </span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {user ? (
            <>
              <Link href="/dashboard" className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised">
                Designs
              </Link>
              <Link href="/drawing" className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised">
                Drawing → CAD
              </Link>
              <Link href="/docs/import" className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-raised">
                Docs
              </Link>
              <Link href="/designs/new" className="btn-primary btn-sm ml-1">
                New design
              </Link>
              <span className="ml-2 hidden border-l border-edge pl-3 text-xs text-slate-500 md:inline">
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
