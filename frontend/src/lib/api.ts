// Browser-side client for the FastAPI backend. Every path goes through the
// /backend/* rewrite in next.config.ts, so the page has a single origin.

export const BACKEND = "/backend";

export type LinkStatus =
  | "waiting"
  | "connecting"
  | "connected"
  | "starting"
  | "active"
  | "failed";

export type Health = {
  connected: boolean;
  has_frames: boolean;
  robot_status: LinkStatus;
  stream_status: LinkStatus;
  vision_active: boolean;
  battery_soc: number | null;
};

export type Velocities = { move: number; rotate: number; strafe: number };

export type VideoMetrics = { stream_fps: number; delay_ms: number | null };

export type VisionStatus = { active: boolean; available: boolean };

export type VoiceState =
  | "off"
  | "waiting"
  | "muted"
  | "hearing"
  | "transcribing";

export type VoiceEvent = {
  at: number;
  heard: string;
  /** "wake", "chat", or the first System 2 tool called: navigate, move, stop */
  intent: string;
  reply: string;
};

export type PttState = "idle" | "prompting" | "recording" | "thinking";

export type PttStatus = {
  state: PttState;
  seconds: number | null;
  last_heard: string | null;
  /** When the last recording was made (epoch seconds); null before the first. */
  clip_at: number | null;
};

export type VoiceStatus = {
  enabled: boolean;
  state: VoiceState;
  follow_up: boolean;
  last_ignored: string | null;
  level?: number;
  recent: VoiceEvent[];
  ptt: PttStatus | null;
};

export type Quaternion = { x: number; y: number; z: number; w: number };

export type RobotState3D = {
  /** Odometry pose in the world (ROS, z-up) frame, or null without odometry. */
  pose: {
    position: { x: number; y: number; z: number };
    orientation: Quaternion;
  } | null;
  /** Leg joint angles (rad) keyed by URDF joint name, or null when stale. */
  joints: Record<string, number> | null;
  at: number;
};

export type WorldObject = {
  id: number;
  label: string;
  /** World (odometry) frame, metres, z up. */
  x: number;
  y: number;
  width: number;
  height: number;
  distance: number;
  confidence: number;
  /** How the distance was measured. */
  source: "lidar" | "ground";
  /** How width/height were measured: lidar points' real extent, or the box. */
  size_source: "lidar" | "camera";
  /** Seconds since last seen. */
  age: number;
};

export type WorldObjects = {
  enabled: boolean;
  available: boolean;
  detect_ms?: number | null;
  objects: WorldObject[];
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND}${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  health: () => request<Health>("/health"),
  reconnect: () => post<{ ok: boolean } | null>("/robot/reconnect"),

  videoFeedUrl: `${BACKEND}/video/feed`,
  videoMetrics: () => request<VideoMetrics>("/video/metrics"),

  visionStatus: () => request<VisionStatus>("/vision/status"),
  visionToggle: () => post<VisionStatus>("/vision/toggle"),

  velocities: () => request<Velocities>("/settings/velocities"),
  setVelocities: (v: Partial<Velocities>) =>
    post<{ ok: boolean } & Velocities>("/settings/velocities", v),

  teleopKey: (key: string, pressed: boolean) =>
    post<{ ok: boolean }>("/teleop/key", { key, pressed }),
  teleopRelease: () => post<{ ok: boolean }>("/teleop/release"),

  speak: (text: string) =>
    post<{ ok: boolean; spoken: boolean }>("/robot/speak", { text }),

  state3d: () => request<RobotState3D>("/robot/state3d"),
  worldObjects: () => request<WorldObjects>("/world/objects"),
  // Lidar voxel map: packed float32 x, y, z (world frame, z up). Binary, not
  // JSON, because it is tens of thousands of points twice a second.
  lidarPoints: async () => {
    const res = await fetch(`${BACKEND}/lidar/points`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return new Float32Array(await res.arrayBuffer());
  },

  voiceStatus: () => request<VoiceStatus>("/voice/status"),
  voiceListen: (enabled: boolean) =>
    post<VoiceStatus>("/voice/listen", { enabled }),

  // Push-to-talk: start when the button goes down, stop when it comes up.
  pttStart: () => post<PttStatus>("/voice/ptt/start"),
  pttStop: () => post<PttStatus>("/voice/ptt/stop"),

  // Records the dog's microphone server-side and returns it as a WAV blob.
  recordMic: async (seconds: number) => {
    const res = await fetch(`${BACKEND}/robot/record?seconds=${seconds}`);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        if (typeof body?.detail === "string") detail = body.detail;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail || `HTTP ${res.status}`);
    }
    return res.blob();
  },

  // Halts everything that can drive the legs: VLA loop, teleop.
  stopAll: () =>
    Promise.allSettled([post("/vla/stop"), post("/teleop/release")]),
};
