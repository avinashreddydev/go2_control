"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type VideoMetrics } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useRobot } from "./robot-context";

function overlayMessage(
  server: string,
  robotStatus: string | undefined,
  streamStatus: string | undefined,
  connected: boolean,
  hasFrames: boolean,
): { text: string; busy: boolean } | null {
  if (server === "starting") return { text: "Starting backend…", busy: true };
  if (server === "offline") return { text: "Backend unreachable", busy: false };
  if (robotStatus === "failed")
    return { text: "Not connected to the robot", busy: false };
  if (!connected) return { text: "Connecting to the robot…", busy: true };
  if (streamStatus === "failed")
    return { text: "Video stream unavailable", busy: false };
  if (!hasFrames) return { text: "Waiting for video…", busy: true };
  return null;
}

function useVideoMetrics(active: boolean) {
  const [metrics, setMetrics] = useState<VideoMetrics | null>(null);
  useEffect(() => {
    if (!active) {
      setMetrics(null);
      return;
    }
    const id = setInterval(() => {
      api.videoMetrics().then(setMetrics, () => setMetrics(null));
    }, 1000);
    return () => clearInterval(id);
  }, [active]);
  return metrics;
}

// Before the first frame we don't know the camera's shape; 16:9 is the common case.
const FALLBACK_RATIO = 16 / 9;

/**
 * The stream's real aspect ratio, read from the image once a frame has
 * decoded. MJPEG streams don't reliably fire `load`, so poll naturalWidth too.
 */
function useNaturalRatio(active: boolean) {
  const ref = useRef<HTMLImageElement>(null);
  const [ratio, setRatio] = useState<number | null>(null);

  const measure = () => {
    const img = ref.current;
    if (img?.naturalWidth && img.naturalHeight) {
      setRatio(img.naturalWidth / img.naturalHeight);
      return true;
    }
    return false;
  };

  useEffect(() => {
    if (!active) return;
    if (measure()) return;
    const id = setInterval(() => {
      if (measure()) clearInterval(id);
    }, 300);
    return () => clearInterval(id);
  });

  return { ref, ratio, measure };
}

export function CameraFeed({ className }: { className?: string }) {
  const { server, health, connected } = useRobot();
  const hasFrames = !!health?.has_frames;
  const overlay = overlayMessage(
    server,
    health?.robot_status,
    health?.stream_status,
    connected,
    hasFrames,
  );
  const metrics = useVideoMetrics(connected && hasFrames);
  const feed = useNaturalRatio(connected && hasFrames);

  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-xl border bg-black",
        className,
      )}
      style={{ aspectRatio: feed.ratio ?? FALLBACK_RATIO }}
    >
      {connected && (
        // MJPEG multipart stream; next/image can't render it.
        // biome-ignore lint/performance/noImgElement: live MJPEG stream
        <img
          ref={feed.ref}
          src={api.videoFeedUrl}
          alt="Robot camera"
          onLoad={feed.measure}
          className="block size-full object-contain"
        />
      )}
      {metrics && (
        <div className="absolute top-2 left-2 rounded bg-black/60 px-2 py-1 font-mono text-white text-xs">
          {metrics.stream_fps} fps · {metrics.delay_ms ?? "--"} ms
        </div>
      )}
      {overlay && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70 text-sm text-white">
          {overlay.busy && <Loader2 className="size-6 animate-spin" />}
          <p>{overlay.text}</p>
        </div>
      )}
    </div>
  );
}
