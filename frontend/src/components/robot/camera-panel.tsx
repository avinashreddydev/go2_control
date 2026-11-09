"use client";

import {
  ExternalLink,
  GripHorizontal,
  PanelRightClose,
  PictureInPicture2,
  Video,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";
import { CameraFeed } from "./camera-feed";

// Chrome's Document Picture-in-Picture: an always-on-top window that can hold
// any DOM, so the live MJPEG <img> can leave the page. Not in TS's DOM lib yet.
type DocumentPiP = {
  requestWindow(options?: { width?: number; height?: number }): Promise<Window>;
};
declare global {
  interface Window {
    documentPictureInPicture?: DocumentPiP;
  }
}

type Mode = "docked" | "floating" | "popout";
type Box = { x: number; y: number; width: number };

const FLOAT_DEFAULT_WIDTH = 320;
const FLOAT_MIN_WIDTH = 220;
const FLOAT_MAX_WIDTH = 900;
const EDGE = 16;

function clampBox(box: Box): Box {
  const width = Math.min(
    Math.max(box.width, FLOAT_MIN_WIDTH),
    Math.min(FLOAT_MAX_WIDTH, window.innerWidth - 2 * EDGE),
  );
  // Height is unknown here (it follows the stream's aspect ratio); keep at
  // least the header on screen.
  return {
    width,
    x: Math.min(Math.max(box.x, EDGE), window.innerWidth - width - EDGE),
    y: Math.min(Math.max(box.y, EDGE), window.innerHeight - 48),
  };
}

function PanelButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      title={label}
      onClick={onClick}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {children}
    </Button>
  );
}

/**
 * The robot camera, docked in the side column by default. It can float as a
 * small draggable, resizable window over the page, or pop out into its own
 * always-on-top window where the browser supports it.
 */
export function CameraPanel() {
  const [mode, setMode] = useState<Mode>("docked");
  const [box, setBox] = useState<Box | null>(null);
  const [pipWindow, setPipWindow] = useState<Window | null>(null);
  const [canPopOut, setCanPopOut] = useState(false);
  const drag = useRef<{
    kind: "move" | "resize";
    startX: number;
    startY: number;
    from: Box;
  } | null>(null);

  // Feature-detect after mount so server and client render the same markup.
  useEffect(() => {
    setCanPopOut(!!window.documentPictureInPicture);
  }, []);

  const closePip = useCallback(() => {
    pipWindow?.close();
    setPipWindow(null);
  }, [pipWindow]);

  const dock = () => {
    closePip();
    setMode("docked");
  };

  const float = () => {
    closePip();
    setBox(
      (prev) =>
        prev ??
        clampBox({
          width: FLOAT_DEFAULT_WIDTH,
          x: window.innerWidth - FLOAT_DEFAULT_WIDTH - 24,
          y: window.innerHeight - 260,
        }),
    );
    setMode("floating");
  };

  const popOut = async () => {
    const api = window.documentPictureInPicture;
    if (!api) return;
    const pip = await api.requestWindow({ width: 420, height: 260 });
    // The new window starts blank: bring over the page's styles and fonts.
    for (const node of document.head.querySelectorAll(
      'style, link[rel="stylesheet"]',
    )) {
      pip.document.head.appendChild(node.cloneNode(true));
    }
    pip.document.documentElement.className = document.documentElement.className;
    pip.document.title = "Boxy camera";
    pip.document.body.className = "m-0 flex min-h-screen items-center bg-black";
    // Closing the window with its own X docks the camera again.
    pip.addEventListener(
      "pagehide",
      () => {
        setPipWindow(null);
        setMode("docked");
      },
      { once: true },
    );
    setPipWindow(pip);
    setMode("popout");
  };

  // Leaving the page closes a popped-out window rather than orphaning it.
  useEffect(() => () => pipWindow?.close(), [pipWindow]);

  // Keep a floating window on screen when the browser is resized.
  useEffect(() => {
    if (mode !== "floating") return;
    const onResize = () => setBox((b) => (b ? clampBox(b) : b));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [mode]);

  const startDrag =
    (kind: "move" | "resize") => (e: React.PointerEvent<HTMLElement>) => {
      if (!box || e.button !== 0) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      drag.current = { kind, startX: e.clientX, startY: e.clientY, from: box };
    };

  const onDrag = (e: React.PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    setBox(
      clampBox(
        d.kind === "move"
          ? { ...d.from, x: d.from.x + dx, y: d.from.y + dy }
          : { ...d.from, width: d.from.width + dx },
      ),
    );
  };

  const endDrag = () => {
    drag.current = null;
  };

  const header = (
    <div className="flex items-center gap-1.5 text-muted-foreground text-sm">
      <Video className="size-4" />
      <span className="font-medium text-foreground">Camera</span>
      <div className="ml-auto flex items-center">
        {mode !== "floating" && (
          <PanelButton label="Float over the page" onClick={float}>
            <PictureInPicture2 />
          </PanelButton>
        )}
        {canPopOut && mode !== "popout" && (
          <PanelButton label="Pop out into its own window" onClick={popOut}>
            <ExternalLink />
          </PanelButton>
        )}
        {mode !== "docked" && (
          <PanelButton label="Dock in the side panel" onClick={dock}>
            <PanelRightClose />
          </PanelButton>
        )}
      </div>
    </div>
  );

  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 rounded-xl bg-card p-3 ring-1 ring-foreground/10">
        {mode === "docked" ? (
          <>
            {header}
            <CameraFeed className="rounded-2xl" />
          </>
        ) : (
          <div className="flex items-center gap-2 text-muted-foreground text-xs">
            <Video className="size-3.5" />
            {mode === "floating"
              ? "Camera is floating"
              : "Camera is in its own window"}
            <Button
              variant="secondary"
              size="xs"
              className="ml-auto"
              onClick={dock}
            >
              Dock here
            </Button>
          </div>
        )}
      </div>

      {/* Portaled to <body>: the sidebar slides with a transform, which would
          otherwise trap a position:fixed window inside it. */}
      {mode === "floating" &&
        box &&
        createPortal(
          <div
            className="fixed z-50 flex flex-col gap-2 rounded-2xl border bg-background p-2 shadow-xl"
            style={{ left: box.x, top: box.y, width: box.width }}
          >
            <div
              className="flex cursor-grab touch-none items-center gap-1 px-1 active:cursor-grabbing"
              onPointerDown={startDrag("move")}
              onPointerMove={onDrag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
            >
              <GripHorizontal className="size-3.5 text-muted-foreground" />
              <div className="flex-1">{header}</div>
            </div>
            <CameraFeed className="rounded-xl" />
            <div
              className="absolute right-0 bottom-0 size-4 cursor-se-resize touch-none"
              title="Drag to resize"
              onPointerDown={startDrag("resize")}
              onPointerMove={onDrag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
            >
              <svg
                viewBox="0 0 16 16"
                className="size-4 text-muted-foreground"
                aria-hidden="true"
              >
                <path
                  d="M14 6 6 14M14 10l-4 4"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </div>
          </div>,
          document.body,
        )}

      {mode === "popout" &&
        pipWindow &&
        createPortal(
          <CameraFeed className="rounded-none border-0" />,
          pipWindow.document.body,
        )}
    </>
  );
}
