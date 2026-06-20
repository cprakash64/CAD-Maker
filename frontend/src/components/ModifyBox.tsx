"use client";

import { useState } from "react";

const EXAMPLES = [
  "make it wider",
  "move the holes farther apart",
  "make the wall thickness 4 mm",
  "add rounded edges",
];

interface Props {
  onSubmit: (prompt: string) => Promise<void>;
  busy: boolean;
}

export default function ModifyBox({ onSubmit, busy }: Props) {
  const [text, setText] = useState("");

  async function go(prompt: string) {
    if (!prompt.trim() || busy) return;
    await onSubmit(prompt.trim());
    setText("");
  }

  return (
    <div className="card p-4">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Edit with a prompt
      </h2>
      <div className="flex gap-2">
        <input
          className="input"
          placeholder="e.g. make it wider"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void go(text);
          }}
          disabled={busy}
        />
        <button className="btn-primary shrink-0" disabled={busy} onClick={() => go(text)}>
          {busy ? "…" : "Apply"}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            className="rounded-full border border-edge px-2 py-1 text-xs text-slate-300 hover:border-accent disabled:opacity-50"
            disabled={busy}
            onClick={() => go(ex)}
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
