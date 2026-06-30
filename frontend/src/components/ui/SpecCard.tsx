import Link from "next/link";

/**
 * Buildable-part preset card. Reads like a machinist's spec card — measurement
 * eyebrow, part name, and a restrained mono spec line — not a marketing tile.
 */
export function SpecCard({
  label,
  prompt,
  href,
  onClick,
  index,
}: {
  label: string;
  prompt: string;
  /** When provided, the whole card is a link (dashboard / landing CTA). */
  href?: string;
  /** When provided, the card is a button (prompt prefill). */
  onClick?: () => void;
  index?: number;
}) {
  const body = (
    <>
      <div className="flex items-center justify-between">
        <span className="stat text-[10px] uppercase tracking-[0.18em] text-slate-500">
          {typeof index === "number" ? `PRESET·${String(index + 1).padStart(2, "0")}` : "PRESET"}
        </span>
        <span
          className="text-slate-600 transition-colors duration-200 group-hover:text-accent"
          aria-hidden
        >
          →
        </span>
      </div>
      <h3 className="mt-3 text-[15px] font-semibold tracking-tight text-slate-100">
        {label}
      </h3>
      <p className="mt-1.5 line-clamp-3 text-[13px] leading-relaxed text-slate-400">
        {prompt}
      </p>
    </>
  );

  const cls =
    "card lift group block h-full p-4 text-left hover:border-[color:var(--glass-border-strong)]";

  if (href) {
    return (
      <Link href={href} className={cls}>
        {body}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} className={`${cls} w-full`}>
      {body}
    </button>
  );
}

export default SpecCard;
