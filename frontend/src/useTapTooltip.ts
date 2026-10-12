// Tooltips that work on touch. A hover tooltip on a link is unreachable on a phone: the tap just
// follows the link. With this hook, a touch (or pen) tap on an item shows its tooltip instead, a
// second tap on the same item follows the link, and a tap anywhere else, or any scroll, closes
// it. A mouse keeps the plain hover-then-click behaviour. See docs/DESIGN.md, "Mobile > Touch".
//
// Spread `itemProps(key)` on each item (a wouter <Link> or <a>); for the item whose key is
// `open.key`, add `openClassName` and merge `openStyle` into its style. The open tooltip is
// pinned to the screen (`position: fixed`, from `--tip-x`/`--tip-y`) rather than to the item,
// because the item usually sits in a sideways-scrolling strip on a phone, and that strip clips
// anything that pokes out of it. Near a screen edge the tooltip anchors to that side; with no
// room above the item it opens below.
import { useEffect, useRef, useState } from "react";

/** How close to a screen edge (px) an item must be for its tooltip to anchor to that edge. */
const EDGE_PX = 110;
/** Room needed above an item (px) for its tooltip to open above it. */
const ROOM_ABOVE_PX = 90;

interface OpenTooltip {
  key: string;
  x: number;
  y: number;
  align: "center" | "left" | "right";
  below: boolean;
}

export function useTapTooltip() {
  const [open, setOpen] = useState<OpenTooltip | null>(null);
  const pointerType = useRef("mouse");

  useEffect(() => {
    if (open == null) return;
    function closeOnOutsideTap(event: PointerEvent) {
      const item = (event.target as Element | null)?.closest?.("[data-tooltip-key]");
      if (item?.getAttribute("data-tooltip-key") !== open?.key) setOpen(null);
    }
    function close() {
      setOpen(null);
    }
    document.addEventListener("pointerdown", closeOnOutsideTap);
    window.addEventListener("scroll", close, { capture: true, passive: true });
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideTap);
      window.removeEventListener("scroll", close, { capture: true });
      window.removeEventListener("resize", close);
    };
  }, [open]);

  function itemProps(key: string) {
    return {
      "data-tooltip-key": key,
      onPointerDown: (event: React.PointerEvent) => {
        pointerType.current = event.pointerType;
      },
      onClick: (event: React.MouseEvent<HTMLElement>) => {
        if (pointerType.current === "mouse" || open?.key === key) return; // follow the link
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        const align =
          rect.left < EDGE_PX
            ? "left"
            : rect.right > window.innerWidth - EDGE_PX
              ? "right"
              : "center";
        const below = rect.top < ROOM_ABOVE_PX;
        setOpen({
          key,
          x:
            align === "left"
              ? rect.left
              : align === "right"
                ? rect.right
                : rect.left + rect.width / 2,
          y: below ? rect.bottom : rect.top,
          align,
          below,
        });
      },
    };
  }

  const openClassName = open
    ? ` is-open is-open--${open.align}${open.below ? " is-open--below" : ""}`
    : "";
  const openStyle = open
    ? ({ "--tip-x": `${open.x}px`, "--tip-y": `${open.y}px` } as React.CSSProperties)
    : {};

  return { openKey: open?.key ?? null, openClassName, openStyle, itemProps };
}
