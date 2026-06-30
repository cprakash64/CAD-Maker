"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { usePartPrompt } from "@/components/PartPromptOverlay";

function Mark() {
  // Compact "precision part" glyph — engineering, not a generic spark. Brass.
  return (
    <span className="grid h-8 w-8 place-items-center rounded-lg border border-edge bg-raised/70 shadow-glass">
      <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden>
        <rect x="1.5" y="1.5" width="13" height="13" rx="2.5" stroke="#d6aa4d" strokeWidth="1.3" />
        <circle cx="8" cy="8" r="2.4" stroke="#afa799" strokeWidth="1.2" />
        <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" stroke="#afa799" strokeWidth="1.2" />
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
      className="hidden items-center gap-1.5 rounded-full border border-edge bg-raised/50 px-2.5 py-1 text-[11px] text-slate-400 md:inline-flex"
      title="Active generation provider"
    >
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
      </span>
      <span className="text-slate-500">Solver</span>
      <span className="text-slate-200">{label}</span>
    </span>
  );
}

function NavLink({
  href,
  active,
  children,
  className = "",
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`relative rounded-lg px-3 py-1.5 text-sm transition-colors duration-200 ${
        active
          ? "text-slate-50"
          : "text-slate-400 hover:bg-raised/60 hover:text-slate-100"
      } ${className}`}
    >
      {children}
      {active && (
        <span className="absolute inset-x-3 -bottom-px h-px bg-gradient-to-r from-transparent via-accent to-transparent" />
      )}
    </Link>
  );
}

export function Header() {
  const { user, logout } = useAuth();
  const partPrompt = usePartPrompt();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const [menuOpen, setMenuOpen] = useState(false);

  const is = (p: string) => pathname === p || pathname.startsWith(p + "/");

  return (
    <header className="sticky top-0 z-40">
      <div className="border-b border-[color:var(--glass-border)] bg-ink/70 backdrop-blur-xl">
        <div className="flex w-full items-center justify-between gap-3 px-4 py-2.5 sm:px-6 lg:px-8">
          <Link
            href={user ? "/dashboard" : "/"}
            className="flex items-center gap-2.5"
            onClick={() => setMenuOpen(false)}
          >
            <Mark />
            <span className="text-[15px] font-semibold tracking-tight text-slate-50">
              LunaiCAD
            </span>
          </Link>

          <nav className="flex items-center gap-1">
            {user ? (
              <>
                {/* Solver/provider status is internal — hide it while inspecting
                    a CAD model on the studio workspace. */}
                {!is("/studio") && <SolverStatus />}
                <div className="hidden items-center gap-0.5 md:flex">
                  <NavLink href="/dashboard" active={is("/dashboard") || is("/studio")}>
                    Workspace
                  </NavLink>
                  <button
                    type="button"
                    onClick={() => partPrompt.open(undefined, { image: true })}
                    className="rounded-lg px-3 py-1.5 text-sm text-slate-400 transition-colors duration-200 hover:bg-raised/60 hover:text-slate-100"
                  >
                    Drawing&nbsp;→&nbsp;CAD
                  </button>
                  <NavLink href="/docs/import" active={is("/docs")} className="hidden lg:block">
                    Docs
                  </NavLink>
                </div>
                <button
                  className="btn-primary btn-sm ml-1.5"
                  onClick={() => partPrompt.open()}
                >
                  New&nbsp;part
                </button>
                <div className="ml-2 hidden items-center gap-2 border-l border-edge pl-3 lg:flex">
                  <span className="max-w-[14ch] truncate text-xs text-slate-500" title={user.email}>
                    {user.email}
                  </span>
                  <button
                    className="rounded-lg px-2.5 py-1.5 text-xs text-slate-400 transition-colors hover:bg-danger/10 hover:text-[#e6a39b] focus-visible:bg-danger/10 focus-visible:text-[#e6a39b]"
                    onClick={() => {
                      logout();
                      router.push("/");
                    }}
                  >
                    Sign out
                  </button>
                </div>
                {/* Mobile menu toggle */}
                <button
                  className="ml-1 grid h-9 w-9 place-items-center rounded-lg border border-edge text-slate-300 md:hidden"
                  aria-label="Menu"
                  aria-expanded={menuOpen}
                  onClick={() => setMenuOpen((o) => !o)}
                >
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
                    <path
                      d={menuOpen ? "M3 3l10 10M13 3L3 13" : "M2 4h12M2 8h12M2 12h12"}
                      stroke="currentColor"
                      strokeWidth="1.4"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
              </>
            ) : (
              <>
                <NavLink href="/signin" active={is("/signin")}>
                  Sign in
                </NavLink>
                <Link href="/signup" className="btn-primary btn-sm ml-1">
                  Get started
                </Link>
              </>
            )}
          </nav>
        </div>
      </div>

      {/* Mobile drawer (authed) */}
      {user && menuOpen && (
        <div className="border-b border-[color:var(--glass-border)] bg-ink/95 backdrop-blur-xl md:hidden">
          <div className="w-full space-y-1 px-4 py-3 sm:px-6 lg:px-8">
            <button
              className="btn-primary btn-sm mb-1 w-full"
              onClick={() => {
                setMenuOpen(false);
                partPrompt.open();
              }}
            >
              New part
            </button>
            <button
              className="block w-full rounded-lg px-3 py-2 text-left text-sm text-slate-300 transition-colors hover:bg-raised/50"
              onClick={() => {
                setMenuOpen(false);
                partPrompt.open(undefined, { image: true });
              }}
            >
              Drawing → CAD
            </button>
            {[
              { href: "/dashboard", label: "Workspace", active: is("/dashboard") || is("/studio") },
              { href: "/docs/import", label: "Docs", active: is("/docs") },
            ].map((l) => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setMenuOpen(false)}
                className={`block rounded-lg px-3 py-2 text-sm transition-colors ${
                  l.active ? "bg-raised/70 text-slate-50" : "text-slate-300 hover:bg-raised/50"
                }`}
              >
                {l.label}
              </Link>
            ))}
            <div className="flex items-center justify-between border-t border-edge pt-3">
              <span className="truncate text-xs text-slate-500">{user.email}</span>
              <button
                className="rounded-lg px-2.5 py-1.5 text-xs text-slate-400 transition-colors hover:bg-danger/10 hover:text-[#e6a39b] focus-visible:bg-danger/10 focus-visible:text-[#e6a39b]"
                onClick={() => {
                  setMenuOpen(false);
                  logout();
                  router.push("/");
                }}
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
