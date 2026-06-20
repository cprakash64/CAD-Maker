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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Your designs</h1>
        <Link href="/designs/new" className="btn-primary">
          New design
        </Link>
      </div>

      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
          Could not reach the API ({error}). Is the backend running on {api.base}?
        </div>
      )}

      {designs && designs.length === 0 && (
        <div className="card space-y-3 p-6">
          <p className="text-slate-300">No designs yet — try an example:</p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {ONBOARDING_EXAMPLES.map((ex) => (
              <Link
                key={ex.label}
                href={`/new?prompt=${encodeURIComponent(ex.prompt)}`}
                className="rounded-md border border-edge p-3 text-sm hover:border-accent"
              >
                <div className="font-medium text-accent">{ex.label}</div>
                <div className="mt-1 text-xs text-slate-300">{ex.prompt}</div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {designs && designs.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-edge">
          <table className="w-full text-sm">
            <thead className="bg-panel text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-2">Prompt</th>
                <th className="px-4 py-2">Template</th>
                <th className="px-4 py-2">Export</th>
                <th className="px-4 py-2">Created</th>
                <th className="px-4 py-2">Edited</th>
              </tr>
            </thead>
            <tbody>
              {designs.map((d) => (
                <tr
                  key={d.id}
                  className="border-t border-edge hover:bg-panel/50"
                >
                  <td className="max-w-xs truncate px-4 py-2">
                    <Link href={`/studio/${d.id}`} className="hover:underline">
                      {d.prompt}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-slate-300">
                    {d.object_type ?? "—"}
                  </td>
                  <td className="px-4 py-2">
                    {d.needs_clarification ? (
                      <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-xs text-amber-200">
                        needs info
                      </span>
                    ) : d.export_ready ? (
                      <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-200">
                        ready
                      </span>
                    ) : (
                      <span className="text-xs text-slate-500">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-400">
                    {new Date(d.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-400">
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
