import { Suspense } from "react";
import { AuthForm } from "@/components/AuthForm";

export default function SignUpPage() {
  return (
    <Suspense fallback={<div className="page text-slate-400">Loading…</div>}>
      <AuthForm mode="signup" />
    </Suspense>
  );
}
