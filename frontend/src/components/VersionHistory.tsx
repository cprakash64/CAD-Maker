"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Design, VersionSummary } from "@/lib/types";
import EditDiff from "./EditDiff";

const EDIT_KIND_LABEL: Record<string, string> = {
  create: "Initial generation",
  regenerate: "Parameters edited",
  modify_prompt: "Edited via prompt",
  localized_edit: "Localized edit",
  face_edit_plan: "Face edit",
  restore: "Restored",
};

function editKindLabel(kind: string): string {
  return EDIT_KIND_LABEL[kind] ?? kind.replace(/_/g, " ");
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/**
 * Version history: list, restore, and "undo last edit". Restoring replays the
 * chosen snapshot through the same validation pipeline as any other edit (see
 * backend app.services.version_service.restore_version) — never a raw
 * client-side rollback of local state.
 */
export default function VersionHistory({
  designId,
  latestVersionNumber,
  busy,
  onRestored,
}: {
  designId: string;
  latestVersionNumber: number | null | undefined;
  busy: boolean;
  onRestored: (design: Design) => void;
}) {
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setVersions(await api.listVersions(designId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load version history");
    } finally {
      setLoading(false);
    }
  }, [designId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  // Reset the panel each time the design changes underneath it.
  useEffect(() => {
    setVersions(null);
    setConfirmId(null);
  }, [latestVersionNumber]);

  const restore = useCallback(
    async (versionId: string) => {
      setRestoringId(versionId);
      setError(null);
      setConfirmId(null);
      try {
        const updated = await api.restoreVersion(designId, versionId);
        onRestored(updated);
        await load();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Restore failed");
      } finally {
        setRestoringId(null);
      }
    },
    [designId, onRestored, load]
  );

  if (!latestVersionNumber) {
    // No edits yet — nothing to show history for (see version_service docs).
    return null;
  }

  const previous = versions?.[1] ?? null;

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          className="label flex items-center gap-1.5 text-slate-300"
          aria-expanded={open}
          aria-controls="version-history-list"
          onClick={() => setOpen((o) => !o)}
        >
          <span aria-hidden>{open ? "▾" : "▸"}</span>
          Version history
        </button>
        {previous && (
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy || restoringId !== null}
            onClick={() => restore(previous.id)}
          >
            Undo last edit
          </button>
        )}
      </div>

      {error && <p className="mt-2 text-[11px] text-[#e6a39b]">{error}</p>}

      {open && (
        <div id="version-history-list" className="mt-3 space-y-2">
          {loading && <p className="text-[11px] text-slate-500">Loading…</p>}
          {!loading && versions && versions.length === 0 && (
            <p className="text-[11px] text-slate-500">No history yet.</p>
          )}
          <ul className="max-h-80 space-y-2 overflow-y-auto">
            {versions?.map((v, i) => {
              const isCurrent = i === 0;
              return (
                <li key={v.id} className="rounded-md border border-edge p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <span className="stat text-xs text-slate-300">v{v.version_number}</span>
                      <span className="text-[11px] text-slate-500">{editKindLabel(v.edit_kind)}</span>
                      {isCurrent && <span className="badge-pass">Current</span>}
                    </div>
                    <span className="text-[11px] text-slate-600">{formatTime(v.created_at)}</span>
                  </div>
                  {v.summary && (
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{v.summary}</p>
                  )}
                  {v.diff.length > 0 && (
                    <div className="mt-1.5">
                      <EditDiff diff={v.diff} title="Changed in this version" />
                    </div>
                  )}
                  {!isCurrent && (
                    <div className="mt-2">
                      {confirmId === v.id ? (
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-amber-200">
                            Restore version {v.version_number}?
                          </span>
                          <button
                            type="button"
                            className="btn-primary btn-sm"
                            disabled={busy || restoringId !== null}
                            onClick={() => restore(v.id)}
                          >
                            {restoringId === v.id ? "Restoring…" : "Confirm"}
                          </button>
                          <button
                            type="button"
                            className="btn-ghost btn-sm"
                            onClick={() => setConfirmId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="btn-ghost btn-sm"
                          disabled={busy || restoringId !== null}
                          onClick={() => setConfirmId(v.id)}
                        >
                          Restore this version
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
