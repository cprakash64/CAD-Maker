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
    <div className="banner-warn">
      <strong className="font-semibold">Offline mock provider.</strong> Prompt and
      image understanding are rule-based, so results are approximate. Set{" "}
      <code className="stat">LLM_PROVIDER=openai</code> with an API key for full
      understanding.
    </div>
  );
}
