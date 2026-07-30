"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { PrivacySummary } from "@/lib/types";
import { SectionHeader } from "@/components/ui/SectionHeader";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SettingsPage() {
  const { user, loading, logout } = useRequireAuth();
  const router = useRouter();

  const [summary, setSummary] = useState<PrivacySummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [optIn, setOptIn] = useState(false);
  const [optInSaving, setOptInSaving] = useState(false);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!user) return;
    api
      .getPrivacySummary()
      .then((s) => {
        setSummary(s);
        setOptIn(s.data_improvement_opt_in);
      })
      .catch((e) => setSummaryError(e instanceof Error ? e.message : String(e)));
  }, [user]);

  const toggleOptIn = async () => {
    const next = !optIn;
    setOptInSaving(true);
    try {
      const res = await api.setDataImprovementOptIn(next);
      setOptIn(res.data_improvement_opt_in);
      setSummary((s) => (s ? { ...s, data_improvement_opt_in: res.data_improvement_opt_in } : s));
    } catch (e) {
      setSummaryError(e instanceof Error ? e.message : String(e));
    } finally {
      setOptInSaving(false);
    }
  };

  const confirmDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteAccount(password);
      logout();
      router.replace("/signin");
    } catch (e) {
      setDeleteError(
        e instanceof ApiError && e.status === 401
          ? "Incorrect password."
          : e instanceof Error
            ? e.message
            : String(e)
      );
    } finally {
      setDeleting(false);
    }
  };

  if (loading || !user) {
    return (
      <div className="page max-w-2xl">
        <div className="h-7 w-40 animate-pulse rounded-md bg-raised/70" />
        <div className="mt-6 h-28 animate-pulse rounded-xl bg-raised/40" />
      </div>
    );
  }

  return (
    <div className="page max-w-2xl space-y-8">
      <SectionHeader
        eyebrow="Account"
        title="Privacy & data"
        description="What LunaiCAD stores for your account, how long, and your controls over it."
      />

      {summaryError && (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm text-amber-200">
          {summaryError}
        </p>
      )}

      {summary && (
        <div className="card space-y-4 p-4">
          <div>
            <p className="label mb-1">What's stored</p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-slate-300">
              <dt className="text-slate-500">Projects</dt>
              <dd>{summary.stored.projects}</dd>
              <dt className="text-slate-500">Designs</dt>
              <dd>{summary.stored.designs}</dd>
              <dt className="text-slate-500">Export file storage</dt>
              <dd>{formatBytes(summary.stored.export_file_bytes)}</dd>
              <dt className="text-slate-500">Account created</dt>
              <dd>{new Date(summary.account_created_at).toLocaleDateString()}</dd>
            </dl>
          </div>

          <div>
            <p className="label mb-1">Retention</p>
            <p className="text-sm leading-relaxed text-slate-400">{summary.retention.note}</p>
          </div>

          <div>
            <p className="label mb-1">Artifact visibility</p>
            <p className="text-sm leading-relaxed text-slate-400">{summary.artifact_visibility}</p>
          </div>
        </div>
      )}

      <div className="card space-y-3 p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-slate-200">
              Use my prompts and designs to improve LunaiCAD's models
            </p>
            <p className="mt-1 text-sm leading-relaxed text-slate-400">
              Off by default for every account. When off, your prompts and designs are
              never used for model improvement.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={optIn}
            onClick={toggleOptIn}
            disabled={optInSaving}
            className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
              optIn ? "bg-emerald-500" : "bg-raised"
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
                optIn ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>
      </div>

      <div className="card space-y-3 border-red-500/30 p-4">
        <p className="text-sm font-medium text-red-300">Delete account</p>
        <p className="text-sm leading-relaxed text-slate-400">
          Permanently deletes your projects, designs, exports, calibration profiles, and
          account. This cannot be undone.
        </p>
        {!deleteOpen ? (
          <button
            type="button"
            onClick={() => setDeleteOpen(true)}
            className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-200 hover:bg-red-500/20"
          >
            Delete my account
          </button>
        ) : (
          <div className="space-y-2">
            <label className="block text-sm text-slate-400">
              Confirm your password to permanently delete your account
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full rounded border border-edge bg-panel px-2 py-1.5 text-sm text-slate-100"
                autoFocus
              />
            </label>
            {deleteError && <p className="text-sm text-red-300">{deleteError}</p>}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={confirmDelete}
                disabled={deleting || !password}
                className="rounded border border-red-500/40 bg-red-500/20 px-3 py-1.5 text-sm font-medium text-red-100 hover:bg-red-500/30 disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Permanently delete"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDeleteOpen(false);
                  setPassword("");
                  setDeleteError(null);
                }}
                className="rounded border border-edge px-3 py-1.5 text-sm text-slate-300 hover:bg-raised/60"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
