import type { ElementType, ReactNode } from "react";

interface GlassPanelProps {
  children: ReactNode;
  className?: string;
  /** "card" = primary smoked-glass surface; "glass" = stronger frost. */
  variant?: "card" | "glass" | "surface";
  /** Add a subtle hover lift (for clickable tiles). */
  interactive?: boolean;
  as?: ElementType;
}

/**
 * The canonical warm smoked-glass surface. Thin wrapper over the `.card` /
 * `.glass` / `.surface` component classes so panels stay visually consistent
 * and can be swapped in one place.
 */
export function GlassPanel({
  children,
  className = "",
  variant = "card",
  interactive = false,
  as: Tag = "div",
}: GlassPanelProps) {
  return (
    <Tag className={`${variant} ${interactive ? "lift" : ""} ${className}`}>
      {children}
    </Tag>
  );
}

export default GlassPanel;
