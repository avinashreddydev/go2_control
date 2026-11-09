"use client";

import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { URDFRobot } from "urdf-loader";
import { useRobot } from "@/components/robot/robot-context";
import { api, type RobotState3D } from "@/lib/api";
import { LidarVoxels } from "./lidar-voxels";
import { SemanticObjects } from "./semantic-objects";

// Two known, harmless three.js warnings, silenced by exact prefix so nothing
// else is hidden: the Z-up note (we undo that rotation in loadMeshCb below,
// once per mesh, ~17 per load) and THREE.Clock's deprecation, which comes
// from inside @react-three/fiber, not from our code.
const QUIET_WARNINGS = [
  "THREE.ColladaLoader: You are loading an asset with a Z-UP coordinate system",
  "THREE.Clock: This module has been deprecated",
];
if (
  typeof window !== "undefined" &&
  !(window as { __quietThree?: boolean }).__quietThree
) {
  (window as { __quietThree?: boolean }).__quietThree = true;
  const warn = console.warn.bind(console);
  console.warn = (...args: unknown[]) => {
    const first = typeof args[0] === "string" ? args[0] : "";
    if (QUIET_WARNINGS.some((prefix) => first.startsWith(prefix))) return;
    warn(...args);
  };
}

// Official Unitree model, fetched by scripts/fetch_go2_model.sh.
const URDF_URL = "/go2/urdf/go2_description.urdf";
const PACKAGES = { go2_description: "/go2" };

const POLL_MS = 100;
// Per-frame blend toward the latest reading: hides the 10 Hz polling steps.
const SMOOTHING = 0.25;

// Go2 standing pose (rad), used before live joint angles arrive.
const STAND: Record<string, number> = {
  hip: 0,
  thigh: 0.67,
  calf: -1.3,
};
const LEGS = ["FR", "FL", "RR", "RL"] as const;
const PARTS = ["hip", "thigh", "calf"] as const;
const STAND_HEIGHT = 0.32; // base height above the floor when standing (m)

type Status = "loading" | "ready" | "error";

/** Live joint angles + pose from the backend, polled while connected. */
function useState3D(active: boolean) {
  const latest = useRef<RobotState3D | null>(null);
  useEffect(() => {
    if (!active) {
      latest.current = null;
      return;
    }
    let stop = false;
    const tick = async () => {
      try {
        latest.current = await api.state3d();
      } catch {
        latest.current = null;
      }
      if (!stop) timer = setTimeout(tick, POLL_MS);
    };
    let timer = setTimeout(tick, 0);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [active]);
  return latest;
}

function Go2({
  live,
  onStatus,
  follow,
  origin,
  robotPos,
}: {
  live: React.RefObject<RobotState3D | null>;
  onStatus: (status: Status) => void;
  follow: React.RefObject<THREE.Vector3>;
  /** Odometry origin shared with the lidar map; set from the first pose. */
  origin: React.RefObject<THREE.Vector3 | null>;
  /** Written every frame: the robot's position in the scene's (ROS) frame. */
  robotPos: React.RefObject<THREE.Vector3>;
}) {
  const [robot, setRobot] = useState<URDFRobot | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Browser-only (DOMParser, ColladaLoader): load lazily on the client.
      const [{ default: URDFLoader }, { ColladaLoader }] = await Promise.all([
        import("urdf-loader"),
        import("three/examples/jsm/loaders/ColladaLoader.js"),
      ]);
      const manager = new THREE.LoadingManager();
      const loader = new URDFLoader(manager);
      loader.packages = PACKAGES;
      // The Go2 meshes are authored Z-up, and ColladaLoader "fixes" that by
      // tilting each mesh -90° about X. Inside a URDF every mesh is already in
      // its (Z-up) link frame, so that tilt would twist the legs and body:
      // undo it. The whole robot is turned Y-up once, by the parent group.
      loader.loadMeshCb = (path, meshManager, material, done) => {
        if (!/\.dae$/i.test(path)) {
          loader.defaultMeshLoader(path, meshManager, material, done);
          return;
        }
        new ColladaLoader(meshManager).load(
          path,
          (dae) => {
            if (!dae) {
              done(null as unknown as THREE.Object3D, new Error(path));
              return;
            }
            dae.scene.rotation.set(0, 0, 0);
            done(dae.scene);
          },
          undefined,
          (err) => done(null as unknown as THREE.Object3D, err as Error),
        );
      };
      loader.load(
        URDF_URL,
        (loaded) => {
          if (cancelled) return;
          manager.onLoad = () => {
            loaded.traverse((child) => {
              if ((child as THREE.Mesh).isMesh) {
                child.castShadow = true;
                child.receiveShadow = true;
              }
            });
            onStatus("ready");
          };
          setRobot(loaded);
        },
        undefined,
        () => !cancelled && onStatus("error"),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [onStatus]);

  useFrame(({ clock }) => {
    if (!robot) return;
    const state = live.current;
    const t = clock.getElapsedTime();

    // Legs: live motor angles, or the standing pose with a slow "breath".
    for (const leg of LEGS) {
      for (const part of PARTS) {
        const name = `${leg}_${part}_joint`;
        const joint = robot.joints[name];
        if (!joint) continue;
        const breath = part === "thigh" ? Math.sin(t * 1.6) * 0.03 : 0;
        const target = state?.joints?.[name] ?? STAND[part] + breath;
        const current = Number(joint.angle) || 0;
        joint.setJointValue(current + (target - current) * SMOOTHING);
      }
    }

    // Body: odometry pose relative to where we started, else standing still.
    const pose = state?.pose;
    if (pose) {
      const p = pose.position;
      origin.current ??= new THREE.Vector3(p.x, p.y, 0);
      const target = new THREE.Vector3(
        p.x - origin.current.x,
        p.y - origin.current.y,
        p.z || STAND_HEIGHT,
      );
      robot.position.lerp(target, SMOOTHING);
      const o = pose.orientation;
      robot.quaternion.slerp(
        new THREE.Quaternion(o.x, o.y, o.z, o.w),
        SMOOTHING,
      );
    } else {
      robot.position.set(0, 0, STAND_HEIGHT + Math.sin(t * 1.6) * 0.004);
      robot.quaternion.identity();
    }

    robotPos.current.copy(robot.position);
    // Where the camera should look, in three.js (y-up) world coordinates.
    robot.getWorldPosition(follow.current);
  });

  return robot ? <primitive object={robot} /> : null;
}

/** A scene layer's on/off state, remembered per browser. */
function useLayer(key: string, initial: boolean) {
  const [on, setOn] = useState(initial);
  useEffect(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved !== null) setOn(saved === "1");
    } catch {}
  }, [key]);
  const set = (next: boolean) => {
    setOn(next);
    try {
      localStorage.setItem(key, next ? "1" : "0");
    } catch {}
  };
  return [on, set] as const;
}

function LayerToggle({
  on,
  onToggle,
  children,
}: {
  on: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onToggle}
      className={
        on
          ? "rounded-full bg-white/90 px-2.5 py-0.5 font-medium text-[11px] text-black"
          : "rounded-full bg-black/50 px-2.5 py-0.5 font-medium text-[11px] text-white/70 hover:text-white"
      }
    >
      {children}
    </button>
  );
}

function FollowCamera({ target }: { target: React.RefObject<THREE.Vector3> }) {
  const controls = useRef<OrbitControlsImpl>(null);
  useFrame(({ camera }) => {
    const c = controls.current;
    if (!c) return;
    // Keep orbiting around the dog as it walks: shift camera and target together.
    const delta = target.current.clone().sub(c.target).multiplyScalar(0.1);
    c.target.add(delta);
    camera.position.add(delta);
    c.update();
  });
  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping
      minDistance={0.6}
      maxDistance={25}
      maxPolarAngle={Math.PI / 2 - 0.05}
    />
  );
}

/**
 * The Go2 rendered from Unitree's URDF, mirroring the real robot: legs follow
 * the live motor angles and the body follows odometry. Without a connection
 * it stands and breathes so the model is still visible.
 */
export function Go2Avatar({ className }: { className?: string }) {
  const { connected } = useRobot();
  const live = useState3D(connected);
  const follow = useRef(new THREE.Vector3());
  // The first odometry pose becomes the centre of the scene, for both the
  // robot and the lidar map, so they always line up.
  const origin = useRef<THREE.Vector3 | null>(null);
  const robotPos = useRef(new THREE.Vector3());
  const [status, setStatus] = useState<Status>("loading");
  const [voxels, setVoxels] = useState(0);
  const [objectCount, setObjectCount] = useState(0);
  const [showObjects, setShowObjects] = useLayer("boxy:3d-objects", true);
  const [showVoxels, setShowVoxels] = useLayer("boxy:3d-voxels", true);
  useEffect(() => {
    if (!connected) origin.current = null; // a reconnect may reset odometry
  }, [connected]);

  return (
    <div className={className} style={{ position: "relative" }}>
      <Canvas
        // PCF: three.js removed PCFSoftShadowMap, R3F's default for `shadows`.
        shadows="percentage"
        dpr={[1, 2]}
        camera={{ position: [1.1, 0.7, 1.3], fov: 40, near: 0.05, far: 100 }}
        style={{ background: "#0b0d10" }}
      >
        <fog attach="fog" args={["#0b0d10", 8, 30]} />
        <hemisphereLight args={["#dfe7ff", "#1a1d22", 0.9]} />
        <directionalLight
          position={[3, 5, 2]}
          intensity={2.2}
          castShadow
          shadow-mapSize={[1024, 1024]}
          shadow-camera-left={-3}
          shadow-camera-right={3}
          shadow-camera-top={3}
          shadow-camera-bottom={-3}
        />
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[60, 60]} />
          <shadowMaterial opacity={0.35} />
        </mesh>
        <Grid
          infiniteGrid
          cellSize={0.25}
          sectionSize={1}
          cellColor="#2a2f37"
          sectionColor="#3b82f6"
          sectionThickness={0.8}
          fadeDistance={25}
          fadeStrength={1.5}
        />
        {/* ROS is z-up, three.js is y-up: the robot and its map live in one
            -90° X rotation, in odometry coordinates. */}
        <group rotation={[-Math.PI / 2, 0, 0]}>
          <Go2
            live={live}
            onStatus={setStatus}
            follow={follow}
            origin={origin}
            robotPos={robotPos}
          />
          <LidarVoxels
            active={connected && showVoxels}
            origin={origin}
            onCount={setVoxels}
          />
          <SemanticObjects
            active={connected && showObjects}
            origin={origin}
            robot={robotPos}
            onCount={setObjectCount}
          />
        </group>
        <FollowCamera target={follow} />
      </Canvas>
      {status !== "ready" && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-white/70">
          {status === "loading"
            ? "Loading Go2 model…"
            : "Go2 model missing: run scripts/fetch_go2_model.sh"}
        </div>
      )}
      <div className="absolute top-2 left-2 flex gap-1">
        <LayerToggle
          on={showObjects}
          onToggle={() => setShowObjects(!showObjects)}
        >
          Objects
        </LayerToggle>
        <LayerToggle
          on={showVoxels}
          onToggle={() => setShowVoxels(!showVoxels)}
        >
          Voxels
        </LayerToggle>
      </div>
      <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-black/50 px-2 py-1 font-mono text-[11px] text-white/80">
        {connected
          ? [
              "live",
              showObjects && `${objectCount} objects`,
              showVoxels && `${voxels.toLocaleString()} voxels`,
            ]
              .filter(Boolean)
              .join(" · ")
          : "offline · standing pose"}
      </div>
    </div>
  );
}
