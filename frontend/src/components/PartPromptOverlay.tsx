"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/* --------------------------------------------------------------------------
   Shared "New CAD part" command surface — a macOS-Spotlight-style prompt
   overlay. One instance is mounted by PartPromptProvider (in the root layout)
   and opened from the homepage CTA, the header, the dashboard and the studio,
   so prompt + image logic lives in exactly one place.

   • Text only  → api.createDesign(text)            → /studio/[id]
   • Text+image → api.generateFromDrawing(file,text) → /studio/[id] (one-shot
     Drawing → CAD); if it can't auto-generate, hands off to /drawing to review.
-------------------------------------------------------------------------- */

interface OpenOpts {
  /** Open with the image attachment emphasised (the "Drawing → CAD" entry). */
  image?: boolean;
}

interface PartPromptCtx {
  open: (initialPrompt?: string, opts?: OpenOpts) => void;
  close: () => void;
  isOpen: boolean;
}

const Ctx = createContext<PartPromptCtx | null>(null);

export function usePartPrompt(): PartPromptCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePartPrompt must be used within PartPromptProvider");
  return ctx;
}

const ACCEPT = "image/png,image/jpeg,image/webp";
const ACCEPT_EXT = /\.(png|jpe?g|webp)$/i;

export function PartPromptProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [seed, setSeed] = useState("");
  const [seedImage, setSeedImage] = useState(false);

  const open = useCallback((initialPrompt = "", opts?: OpenOpts) => {
    setSeed(initialPrompt);
    setSeedImage(!!opts?.image);
    setIsOpen(true);
  }, []);
  const close = useCallback(() => setIsOpen(false), []);

  return (
    <Ctx.Provider value={{ open, close, isOpen }}>
      {children}
      {isOpen && <Overlay seed={seed} emphasizeImage={seedImage} onClose={close} />}
    </Ctx.Provider>
  );
}

function Mark() {
  return (
    <span className="grid h-5 w-5 place-items-center rounded-md border border-edge bg-raised/70">
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
        <rect x="1.5" y="1.5" width="13" height="13" rx="2.5" stroke="#d6aa4d" strokeWidth="1.4" />
        <circle cx="8" cy="8" r="2.4" stroke="#afa799" strokeWidth="1.3" />
      </svg>
    </span>
  );
}

function ImageIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="5.5" cy="6" r="1.1" fill="currentColor" />
      <path d="M2.5 12l3.2-3.4 2.3 2.2 2.2-2.4 3.3 3.6" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

function autosize(ta: HTMLTextAreaElement) {
  ta.style.height = "0px";
  const max = Math.round(window.innerHeight * 0.4);
  const next = Math.min(ta.scrollHeight, max);
  ta.style.height = `${next}px`;
  ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
}

function Overlay({
  seed,
  emphasizeImage,
  onClose,
}: {
  seed: string;
  emphasizeImage: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const { user } = useAuth();
  const [value, setValue] = useState(seed);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [gate, setGate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);

  // Autofocus + size the input on open; caret to end when seeded.
  useEffect(() => {
    const t = setTimeout(() => {
      const ta = taRef.current;
      if (!ta) return;
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
      autosize(ta);
    }, 20);
    return () => clearTimeout(t);
  }, []);

  // Esc closes; lock body scroll while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  // Manage the preview object URL lifecycle.
  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function acceptFile(f: File | null | undefined) {
    if (!f) return;
    if (!f.type.startsWith("image/") && !ACCEPT_EXT.test(f.name)) {
      setError("Unsupported file — use a PNG, JPG or WEBP image.");
      return;
    }
    setError(null);
    setFile(f);
  }

  function clearImage() {
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  const trimmed = value.trim();
  const promptQuery = trimmed ? `?prompt=${encodeURIComponent(trimmed)}` : "";
  const canSubmit = !!trimmed || !!file;

  async function submit() {
    if (!canSubmit) {
      taRef.current?.focus();
      return;
    }
    // Logged-out: gate to auth without losing the spec (the image must be
    // re-attached after sign-in — binaries can't ride through query params).
    if (!user) {
      setGate(true);
      return;
    }
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      if (file) {
        // One-shot Drawing → CAD: interpret + generate, text as guidance.
        const res = await api.generateFromDrawing(file, trimmed || undefined);
        if (res.generated && res.design) {
          onClose();
          router.push(`/studio/${res.design.id}`);
          return;
        }
        // Couldn't auto-generate — hand off to the full Drawing → CAD review.
        setError("Couldn't auto-generate from this image. Opening Drawing → CAD to review…");
        setTimeout(() => {
          onClose();
          router.push("/drawing");
        }, 1100);
        return;
      }
      const design = await api.createDesign(trimmed);
      onClose();
      router.push(`/studio/${design.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `Generation failed: ${String(e)}`);
      setBusy(false);
      submitting.current = false;
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[15vh] sm:pt-[17vh]"
      role="dialog"
      aria-modal="true"
      aria-label="New CAD part"
      // Click-outside closes only when there's nothing to lose.
      onMouseDown={() => {
        if (!trimmed && !file) onClose();
      }}
    >
      <div className="absolute inset-0 bg-ink/70 backdrop-blur-md animate-fade-in" aria-hidden />

      <div
        className="relative w-full max-w-2xl animate-overlay-in"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div
          className={`relative overflow-hidden border shadow-lift backdrop-blur-2xl transition-[border-radius] duration-200 ${
            value.length > 0 || file ? "rounded-3xl" : "rounded-[30px]"
          }`}
          style={{
            background: "var(--glass-fill-strong)",
            borderColor: dragOver ? "rgba(214,170,77,0.7)" : "var(--glass-border-strong)",
            boxShadow:
              "0 1px 0 0 rgba(255,248,235,0.06) inset, 0 0 0 1px rgba(214,170,77,0.16), 0 30px 70px -30px rgba(0,0,0,0.9)",
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (!gate) setDragOver(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            setDragOver(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (!gate) acceptFile(e.dataTransfer.files?.[0]);
          }}
        >
          {/* Drag affordance */}
          {dragOver && !gate && (
            <div className="pointer-events-none absolute inset-0 z-10 m-2 grid place-items-center rounded-[26px] border-2 border-dashed border-accent/70 bg-ink/60 backdrop-blur-sm">
              <span className="text-sm font-medium text-accent">Drop image to attach</span>
            </div>
          )}

          {!gate ? (
            <>
              <div className="flex items-center gap-2 px-5 pt-4">
                <Mark />
                <span className="label text-slate-400">New CAD part</span>
                {emphasizeImage && (
                  <span className="badge-neutral ml-1">Image → CAD</span>
                )}
              </div>

              <div className="px-5 py-3">
                <textarea
                  ref={taRef}
                  rows={1}
                  className="block w-full resize-none border-0 bg-transparent p-0 text-[17px] leading-relaxed text-slate-50 placeholder:text-slate-600 outline-none"
                  placeholder="Describe a bracket, enclosure, jig, flange, clamp, spacer, or gear…"
                  value={value}
                  disabled={busy}
                  onChange={(e) => {
                    setValue(e.target.value);
                    autosize(e.target);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
              </div>

              {/* Compact image preview (only when attached) */}
              {file && previewUrl && (
                <div className="mx-5 mb-1 flex items-center gap-3 rounded-xl border border-edge bg-raised/40 p-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={previewUrl}
                    alt="Attached reference"
                    className="h-12 w-12 shrink-0 rounded-lg border border-edge object-cover"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-200">{file.name}</div>
                    <div className="stat text-[10px] text-slate-500">
                      {(file.size / 1024).toFixed(0)} KB · guides the interpretation
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={clearImage}
                    disabled={busy}
                    className="shrink-0 rounded-md px-2 py-1 text-xs text-slate-500 transition-colors hover:bg-raised hover:text-slate-200"
                    aria-label="Remove image"
                  >
                    Remove
                  </button>
                </div>
              )}

              <div className="flex items-center justify-between gap-3 border-t border-edge/70 px-5 py-3">
                <div className="flex min-w-0 items-center gap-2.5">
                  <button
                    type="button"
                    onClick={() => fileRef.current?.click()}
                    disabled={busy}
                    className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
                      emphasizeImage && !file
                        ? "border-accent/60 bg-accent/10 text-accent"
                        : "border-edge bg-raised/40 text-slate-300 hover:border-accent/50 hover:text-slate-100"
                    }`}
                  >
                    <ImageIcon />
                    {file ? "Change image" : "Add image"}
                  </button>
                  <span className="hidden min-w-0 truncate text-xs text-slate-500 sm:block">
                    {file
                      ? "Image attached — text refines the interpretation."
                      : "Add a sketch, drawing, or reference image."}
                  </span>
                  <input
                    ref={fileRef}
                    type="file"
                    accept={ACCEPT}
                    className="hidden"
                    onChange={(e) => acceptFile(e.target.files?.[0])}
                  />
                </div>

                <div className="flex shrink-0 items-center gap-3">
                  <span className="hidden items-center gap-1.5 text-[11px] text-slate-600 lg:flex">
                    <Kbd>Esc</Kbd>
                    <span>close</span>
                    <span className="text-slate-700">·</span>
                    <Kbd>⌘↵</Kbd>
                    <span>generate</span>
                  </span>
                  <button
                    className="btn-primary btn-sm"
                    onClick={submit}
                    disabled={busy || !canSubmit}
                  >
                    {busy ? "Generating…" : "Generate part"}
                  </button>
                </div>
              </div>

              {error && (
                <div className="border-t border-edge/70 px-5 py-2.5">
                  <p className="text-xs text-[#e6a39b]">{error}</p>
                </div>
              )}
            </>
          ) : (
            <div className="p-5">
              <div className="flex items-center gap-2">
                <Mark />
                <span className="label text-slate-400">New CAD part</span>
              </div>
              <p className="mt-3 text-sm font-medium text-slate-100">
                Create an account to generate and save this CAD part.
              </p>
              <p className="mt-1 text-xs leading-relaxed text-slate-400">
                Sign in to generate this part and export STEP/STL — your spec is kept.
              </p>
              {trimmed && (
                <div className="mt-3 rounded-lg border border-edge bg-raised/40 p-3">
                  <span className="label mb-1 block text-slate-500">Your spec</span>
                  <p className="line-clamp-3 text-xs leading-relaxed text-slate-300">{trimmed}</p>
                </div>
              )}
              {file && (
                <p className="mt-2 text-xs text-slate-500">
                  Your image isn’t carried through sign-in — re-attach it after you’re in.
                </p>
              )}
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Link href={`/signup${promptQuery}`} className="btn-primary btn-sm" onClick={onClose}>
                  Create account
                </Link>
                <Link href={`/signin${promptQuery}`} className="btn-ghost btn-sm" onClick={onClose}>
                  Sign in
                </Link>
                <button
                  className="ml-auto text-xs text-slate-500 transition-colors hover:text-slate-300"
                  onClick={() => setGate(false)}
                >
                  ← Edit spec
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-edge bg-raised/70 px-1.5 py-0.5 font-sans text-[10px] text-slate-400">
      {children}
    </kbd>
  );
}
