"use client";

import { Box, Maximize2, Minimize2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";

// WebGL only exists in the browser; skip server rendering for the 3D view.
const Go2Avatar = dynamic(
  () => import("./go2-avatar").then((m) => m.Go2Avatar),
  {
    ssr: false,
    loading: () => (
      <div className="flex size-full items-center justify-center bg-[#0b0d10] text-sm text-white/60">
        Starting 3D view…
      </div>
    ),
  },
);

/** The Go2 avatar in the sidebar, expandable to a full-screen view. */
export function AvatarPanel({
  viewClassName = "aspect-[4/3]",
}: {
  /** Sizes the small (docked) 3D view. */
  viewClassName?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) =>
      e.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  return (
    <div className="flex shrink-0 flex-col gap-2 rounded-xl bg-card p-3 ring-1 ring-foreground/10">
      <div className="flex items-center gap-1.5 text-muted-foreground text-sm">
        <Box className="size-4" />
        <span className="font-medium text-foreground">3D avatar</span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          aria-label="Expand 3D view"
          title="Expand"
          onClick={() => setExpanded(true)}
        >
          <Maximize2 />
        </Button>
      </div>
      {/* One WebGL view at a time: the small one unmounts while expanded. */}
      {expanded ? (
        <div
          className={`flex ${viewClassName} items-center justify-center rounded-lg bg-muted text-muted-foreground text-xs`}
        >
          Showing full screen
        </div>
      ) : (
        <Go2Avatar className={`${viewClassName} overflow-hidden rounded-lg`} />
      )}

      {expanded &&
        createPortal(
          <div className="fixed inset-0 z-50 bg-[#0b0d10]">
            <Go2Avatar className="size-full" />
            <Button
              variant="secondary"
              size="sm"
              className="absolute top-4 right-4"
              onClick={() => setExpanded(false)}
            >
              <Minimize2 data-icon="inline-start" />
              Close
            </Button>
          </div>,
          document.body,
        )}
    </div>
  );
}
