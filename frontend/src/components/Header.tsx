"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export function Header() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="border-b border-edge">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <Link href="/" className="font-semibold tracking-tight">
          SourceCAD <span className="text-accent">AI Part Studio</span>
        </Link>
        <nav className="flex items-center gap-2 text-sm">
          {user ? (
            <>
              <Link href="/dashboard" className="btn-ghost">
                Dashboard
              </Link>
              <Link href="/drawing" className="btn-ghost">
                Drawing→CAD
              </Link>
              <Link href="/docs/import" className="btn-ghost">
                Docs
              </Link>
              <Link href="/designs/new" className="btn-primary">
                New design
              </Link>
              <span className="ml-1 hidden text-xs text-slate-400 sm:inline">
                {user.email}
              </span>
              <button
                className="btn-ghost"
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
              <Link href="/signin" className="btn-ghost">
                Sign in
              </Link>
              <Link href="/signup" className="btn-primary">
                Sign up
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
