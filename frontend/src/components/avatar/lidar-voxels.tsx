"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { api } from "@/lib/api";

// Matches the Go2 lidar map resolution and the backend's LIDAR_MAX_POINTS.
const VOXEL = 0.05;
const MAX_VOXELS = 30000;
const POLL_MS = 500;

// The floor is not drawn: voxels within this height of it are skipped.
const FLOOR_CLEARANCE = 0.08;
// Height colouring (metres above the floor): deep blue at knee height rising
// to near-white at head height, like a driving display.
const TOP_Z = 1.8;
const LOW = new THREE.Color("#1d4ed8");
const HIGH = new THREE.Color("#e0f2fe");

function colorFor(height: number, out: THREE.Color) {
  const t = Math.min(
    1,
    Math.max(0, (height - FLOOR_CLEARANCE) / (TOP_Z - FLOOR_CLEARANCE)),
  );
  return out.copy(LOW).lerp(HIGH, t);
}

/**
 * Floor height: the low end of the voxel heights. Odometry's z=0 is not
 * guaranteed to be the floor, so measure it; the floor is usually the
 * largest flat surface the lidar sees. Sampled for speed.
 */
function floorHeight(points: Float32Array): number {
  const n = points.length / 3;
  if (n === 0) return 0;
  const step = Math.max(1, Math.floor(n / 2000));
  const zs: number[] = [];
  for (let i = 0; i < n; i += step) zs.push(points[i * 3 + 2]);
  zs.sort((a, b) => a - b);
  return zs[Math.floor(zs.length * 0.05)];
}

/**
 * The robot's live lidar voxel map as small cubes, in the same ROS (z-up)
 * frame as the avatar: render it inside the avatar's rotated group. Points
 * arrive in the odometry frame and are shifted by the same origin as the
 * robot, so the dog walks through the map it is building.
 */
export function LidarVoxels({
  active,
  origin,
  onCount,
}: {
  active: boolean;
  origin: React.RefObject<THREE.Vector3 | null>;
  onCount?: (count: number) => void;
}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const [geometry] = useState(() => {
    const size = VOXEL * 0.9; // a hairline gap keeps the blocks readable
    return new THREE.BoxGeometry(size, size, size);
  });
  const [material] = useState(
    () => new THREE.MeshStandardMaterial({ roughness: 0.85, metalness: 0 }),
  );

  useEffect(() => () => geometry.dispose(), [geometry]);
  useEffect(() => () => material.dispose(), [material]);

  useEffect(() => {
    const m = mesh.current;
    if (!active) {
      if (m) m.count = 0;
      onCount?.(0);
      return;
    }

    const matrix = new THREE.Matrix4();
    const color = new THREE.Color();
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;

    const apply = (points: Float32Array) => {
      const target = mesh.current;
      const o = origin.current;
      // Until the first odometry pose fixes the origin, the map has no anchor.
      if (!target || !o) return;
      const floor = floorHeight(points);
      const total = Math.min(points.length / 3, MAX_VOXELS);
      let n = 0;
      for (let i = 0; i < total; i++) {
        const z = points[i * 3 + 2];
        const height = z - floor;
        if (height <= FLOOR_CLEARANCE) continue; // don't draw the floor
        matrix.makeTranslation(points[i * 3] - o.x, points[i * 3 + 1] - o.y, z);
        target.setMatrixAt(n, matrix);
        target.setColorAt(n, colorFor(height, color));
        n++;
      }
      target.count = n;
      target.instanceMatrix.needsUpdate = true;
      if (target.instanceColor) target.instanceColor.needsUpdate = true;
      onCount?.(n);
    };

    const tick = async () => {
      try {
        apply(await api.lidarPoints());
      } catch {
        /* backend busy or offline; try again next tick */
      }
      if (!stop) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, 0);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [active, origin, onCount]);

  return (
    <instancedMesh
      ref={mesh}
      args={[geometry, material, MAX_VOXELS]}
      count={0}
      // Instances move every update; skip the (stale) bounding-sphere test.
      frustumCulled={false}
      castShadow
      receiveShadow
    />
  );
}
