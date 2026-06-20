"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { ONBOARDING_EXAMPLES } from "@/lib/examples";
import type { DesignSummary } from "@/lib/types";

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString();
}

export default function DashboardPage() {
  const { user, loading } = useRequireAuth();
  const [designs, setDesigns] = useState<DesignSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api
      .listDesigns()
      .then(setDesigns)
      .catch((e) => setError(String(e?.message ?? e)));
  }, [user]);

  if (loading || !user) {
    return <div className="py-10 text-slate-400">Loading…</div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <span className="label">Workspace</span>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-50">Designs</h1>
        </div>
        <Link href="/designs/new" className="btn-primary">
          New design
        </Link>
      </div>

      {error && (
        <div className="banner-danger">
          Could not reach the API ({error}). Is the backend running on {api.base}?
        </div>
      )}

      {designs && designs.length === 0 && (
        <div className="card space-y-4 p-6">
          <p className="text-sm text-slate-300">
            No designs yet. Start from a prompt, or pick an example:
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {ONBOARDING_EXAMPLES.map((ex) => (
              <Link
                key={ex.label}
                href={`/new?prompt=${encodeURIComponent(ex.prompt)}`}
                className="surface p-3 text-sm transition-colors hover:border-accent/60"
              >
                <div className="label text-slate-300">{ex.label}</div>
                <div className="mt-1 text-xs text-slate-400">{ex.prompt}</div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {designs && designs.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-edge bg-raised/60 text-left text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-2.5 font-semibold">Part</th>
                <th className="px-4 py-2.5 font-semibold">Type</th>
                <th className="px-4 py-2.5 font-semibold">Status</th>
                <th className="px-4 py-2.5 font-semibold">Created</th>
                <th className="px-4 py-2.5 font-semibold">Edited</th>
              </tr>
            </thead>
            <tbody>
              {designs.map((d) => (
                <tr key={d.id} className="border-t border-edge/70 hover:bg-raised/40">
                  <td className="max-w-sm truncate px-4 py-3">
                    <Link
                      href={`/studio/${d.id}`}
                      className="text-slate-200 hover:text-accent hover:underline"
                    >
                      {d.prompt}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{d.object_type ?? "—"}</td>
                  <td className="px-4 py-3">
                    {d.needs_clarification ? (
                      <span className="badge-review">Needs info</span>
                    ) : d.export_ready ? (
                      <span className="badge-pass">Ready</span>
                    ) : (
                      <span className="badge-neutral">Draft</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {new Date(d.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {timeAgo(d.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
