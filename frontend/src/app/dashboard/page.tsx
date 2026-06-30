"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { DesignSummary } from "@/lib/types";
import { SectionHeader } from "@/components/ui/SectionHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { usePartPrompt } from "@/components/PartPromptOverlay";

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

function statusOf(d: DesignSummary): "ready" | "draft" | "needs_info" {
  if (d.needs_clarification) return "needs_info";
  if (d.export_ready) return "ready";
  return "draft";
}

export default function DashboardPage() {
  const { user, loading } = useRequireAuth();
  const partPrompt = usePartPrompt();
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
    return (
      <div className="page max-w-5xl">
        <div className="h-7 w-40 animate-pulse rounded-md bg-raised/70" />
        <div className="mt-6 h-28 animate-pulse rounded-xl bg-raised/40" />
      </div>
    );
  }

  const recent = (designs ?? []).slice(0, 6);

  return (
    <div className="page max-w-5xl space-y-8">
      <SectionHeader
        eyebrow="Workspace"
        title="Your CAD parts"
        description="Open a part to inspect and export it, or start a new one — describe it in plain English and LunaiCAD builds the geometry."
        action={
          <button className="btn-primary" onClick={() => partPrompt.open()}>
            New part
          </button>
        }
      />

      {error && (
        <div className="banner-danger">
          <span className="font-semibold">Could not reach the API.</span> {error}.
          Is the backend running on {api.base}?
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-end justify-between">
          <h2 className="label">Recent parts</h2>
          {(designs?.length ?? 0) > recent.length && (
            <span className="text-xs text-slate-500">
              Showing {recent.length} of {designs!.length}
            </span>
          )}
        </div>

        {!designs && !error && (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-14 animate-pulse rounded-lg bg-raised/40" />
            ))}
          </div>
        )}

        {designs && designs.length === 0 && (
          <div className="rounded-xl border border-edge bg-raised/30 px-6 py-10 text-center">
            <p className="text-sm text-slate-400">
              No parts yet — your generated parts will appear here.
            </p>
            <button className="btn-primary mt-4" onClick={() => partPrompt.open()}>
              Describe your first part
            </button>
          </div>
        )}

        {recent.length > 0 && (
          <div className="card divide-y divide-edge/50 overflow-hidden">
            {recent.map((d) => (
              <Link
                key={d.id}
                href={`/studio/${d.id}`}
                className="group flex items-center gap-4 px-4 py-3 transition-colors hover:bg-raised/40"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-slate-200 transition-colors group-hover:text-accent">
                    {d.title ?? (d.object_type ? d.object_type.replace(/_/g, " ") : d.prompt)}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-slate-500">{d.prompt}</div>
                </div>
                <StatusBadge status={statusOf(d)} className="shrink-0" />
                <span className="stat hidden w-16 shrink-0 text-right text-[11px] text-slate-500 sm:block">
                  {timeAgo(d.updated_at)}
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
