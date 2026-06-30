import { Suspense } from "react";
import { AuthForm } from "@/components/AuthForm";

export default function SignInPage() {
  return (
    <Suspense fallback={<div className="page text-slate-400">Loading…</div>}>
      <AuthForm mode="signin" />
    </Suspense>
  );
}
