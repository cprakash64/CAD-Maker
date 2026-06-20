"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function MockModeBanner() {
  const [provider, setProvider] = useState<string | null>(null);

  useEffect(() => {
    api.health().then((h) => setProvider(h.llm_provider ?? null)).catch(() => {});
  }, []);

  if (provider !== "mock") return null;
  return (
    <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm text-amber-100">
      <strong>Mock mode.</strong> Prompt and image understanding are limited and
      rule-based — results are approximate. Set{" "}
      <code>LLM_PROVIDER=openai</code> with an API key for full understanding.
    </div>
  );
}
