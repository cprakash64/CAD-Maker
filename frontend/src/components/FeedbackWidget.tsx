"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { FEEDBACK_CATEGORIES, type Feedback } from "@/lib/types";

interface Props {
  designId: string;
  existing: Feedback | null;
}

export default function FeedbackWidget({ designId, existing }: Props) {
  const [rating, setRating] = useState<"up" | "down" | null>(
    existing?.rating ?? null
  );
  const [categories, setCategories] = useState<string[]>(
    existing?.categories ?? []
  );
  const [comment, setComment] = useState(existing?.comment ?? "");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(cat: string) {
    setCategories((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]
    );
  }

  async function submit(r: "up" | "down") {
    setRating(r);
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api.submitFeedback(designId, r, r === "down" ? categories : [], comment);
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save feedback");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Was this useful?
      </h2>
      <div className="flex gap-2">
        <button
          className={`btn-ghost ${rating === "up" ? "border-emerald-500 text-emerald-300" : ""}`}
          onClick={() => submit("up")}
          disabled={busy}
        >
          👍 Yes
        </button>
        <button
          className={`btn-ghost ${rating === "down" ? "border-red-500 text-red-300" : ""}`}
          onClick={() => setRating("down")}
          disabled={busy}
        >
          👎 Needs work
        </button>
      </div>

      {rating === "down" && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {FEEDBACK_CATEGORIES.map((c) => (
              <button
                key={c.value}
                onClick={() => toggle(c.value)}
                className={`rounded-full border px-2 py-1 text-xs ${
                  categories.includes(c.value)
                    ? "border-accent text-accent"
                    : "border-edge text-slate-300"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <textarea
            className="input min-h-[60px] resize-y text-sm"
            placeholder="Anything else? (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <button className="btn-primary" onClick={() => submit("down")} disabled={busy}>
            {busy ? "Saving…" : "Submit feedback"}
          </button>
        </div>
      )}

      {saved && <p className="mt-2 text-xs text-emerald-300">Thanks — feedback saved.</p>}
      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
    </div>
  );
}
