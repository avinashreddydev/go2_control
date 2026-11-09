"use client";

import { Edges, Html, useAnimations, useGLTF } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";
import { api, type WorldObject } from "@/lib/api";

const POLL_MS = 300;
// Per-frame blend toward the latest position: objects glide, not jump.
const SMOOTHING = 0.15;
// Above this speed (m/s) an animated model plays its walk cycle.
const WALKING_SPEED = 0.15;

// glTF models from the Khronos sample assets (scripts/fetch_object_models.sh).
// Each detected label is drawn with the first model whose list contains it.
type ModelSpec = {
  file: string;
  labels: string[];
  /** Real-world height range for this kind of object, metres. A measurement
   *  outside it is clamped (a cut-off box can't make a 10 cm sofa). */
  min: number;
  max: number;
  /** Used when nothing usable was measured. */
  typical: number;
  /** Only these mesh nodes are drawn (the asset is a demo scene). */
  keep?: string[];
  /** Mesh nodes whose names start with these are dropped. */
  drop?: string[];
  /** False when the visible width depends on pose or angle (people, pets). */
  sizeByWidth?: boolean;
  /** Clip while moving; "*" = the first clip. */
  walk?: string;
  /** Clip while still; "freeze" = hold the walk clip mid-stride. */
  still?: string;
};

const MODELS: ModelSpec[] = [
  {
    file: "CesiumMan",
    labels: [
      "person",
      "man",
      "woman",
      "boy",
      "girl",
      "child",
      "people",
      "human",
      "pedestrian",
    ],
    typical: 1.7,
    min: 1.1,
    max: 2.0,
    sizeByWidth: false,
    walk: "*",
    // Its rest pose is a T-pose; a frozen stride reads as standing.
    still: "freeze",
  },
  {
    file: "SheenChair",
    labels: [
      "chair",
      "armchair",
      "office chair",
      "folding chair",
      "stool",
      "seat",
      "dining chair",
    ],
    typical: 0.9,
    min: 0.45,
    max: 1.2,
  },
  {
    file: "GlamVelvetSofa",
    labels: ["sofa", "couch", "loveseat", "settee"],
    typical: 0.8,
    min: 0.6,
    max: 1.1,
  },
  {
    file: "DiffuseTransmissionPlant",
    labels: [
      "potted plant",
      "plant",
      "houseplant",
      "flowerpot",
      "pot plant",
      "flower pot",
    ],
    typical: 0.8,
    min: 0.25,
    max: 1.8,
    drop: ["firefly"],
  },
  {
    file: "GlassVaseFlowers",
    labels: ["vase", "flower", "flowers", "bouquet", "flower vase"],
    typical: 0.35,
    min: 0.15,
    max: 0.8,
    keep: ["GlassTransmission", "Flowers2"],
  },
  {
    file: "DiffuseTransmissionTeacup",
    labels: ["cup", "mug", "teacup", "tea cup", "coffee cup"],
    typical: 0.09,
    min: 0.06,
    max: 0.15,
  },
  {
    file: "WaterBottle",
    labels: ["bottle", "water bottle", "plastic bottle", "drink bottle"],
    typical: 0.25,
    min: 0.15,
    max: 0.4,
  },
  {
    file: "TrafficCone",
    labels: ["traffic cone", "cone", "safety cone"],
    typical: 0.5,
    min: 0.3,
    max: 1.0,
    keep: ["Cone Normal"],
  },
  {
    file: "Fox",
    labels: ["dog", "cat", "fox", "animal", "puppy", "pet", "kitten"],
    typical: 0.5,
    min: 0.25,
    max: 0.9,
    sizeByWidth: false,
    walk: "Walk",
    still: "Survey",
  },
];

const BY_LABEL = new Map(
  MODELS.flatMap((spec) => spec.labels.map((label) => [label, spec] as const)),
);
const url = (spec: ModelSpec) => `/models/${spec.file}.glb`;

// Start downloading all models up front, so the first detection is instant.
// (No Draco/meshopt: these assets don't use them, and it avoids a CDN fetch.)
if (typeof window !== "undefined") {
  for (const spec of MODELS) useGLTF.preload(url(spec), false, false);
}

const PALETTE = [
  "#60a5fa",
  "#34d399",
  "#fbbf24",
  "#f472b6",
  "#a78bfa",
  "#2dd4bf",
  "#fb923c",
];
function colorFor(label: string) {
  let hash = 0;
  for (const ch of label) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(Math.max(v, lo), hi);

function Label({ object, height }: { object: WorldObject; height: number }) {
  return (
    <Html
      position={[0, 0, height + 0.15]}
      center
      zIndexRange={[20, 0]}
      style={{ pointerEvents: "none" }}
    >
      <div className="whitespace-nowrap rounded-full bg-black/70 px-2 py-0.5 font-medium text-[11px] text-white shadow">
        {object.label}
        <span className="ml-1 text-white/60">
          {object.distance.toFixed(1)} m
        </span>
      </div>
    </Html>
  );
}

/**
 * The object's real height, from the detection:
 * 1. its measured height, when plausible for this kind of object (vertical
 *    size doesn't depend on which way the object is turned);
 * 2. else its measured width times this model's height/width ratio (e.g. the
 *    box was cut off at the frame edge), for objects whose width is stable;
 * 3. else the typical height for this kind of object;
 * then held to the real-world range.
 */
function fitHeight(
  spec: ModelSpec,
  object: WorldObject,
  modelHeight: number,
  modelWidth: number,
) {
  const plausible = (h: number) =>
    Number.isFinite(h) && h >= spec.min * 0.75 && h <= spec.max * 1.33;
  let height = object.height;
  if (!plausible(height)) {
    const fromWidth =
      spec.sizeByWidth !== false && object.width > 0.03
        ? (object.width * modelHeight) / modelWidth
        : Number.NaN;
    height = plausible(fromWidth) ? fromWidth : spec.typical;
  }
  return clamp(height, spec.min, spec.max);
}

/** A real glTF model standing on the floor, sized to match the detection. */
function GltfModel({
  spec,
  object,
  moving,
}: {
  spec: ModelSpec;
  object: WorldObject;
  moving: React.RefObject<boolean>;
}) {
  const gltf = useGLTF(url(spec), false, false);
  const root = useRef<THREE.Group>(null);
  const sizer = useRef<THREE.Group>(null);

  // One copy per detected object (SkeletonUtils keeps skinned meshes working),
  // trimmed to the object itself: bundled lights, ground planes and demo
  // extras are removed before it is measured.
  const { scene, offset, height, width } = useMemo(() => {
    const copy = cloneSkinned(gltf.scene) as THREE.Object3D;
    const remove: THREE.Object3D[] = [];
    copy.traverse((child) => {
      if ((child as THREE.Light).isLight) remove.push(child);
      if (!(child as THREE.Mesh).isMesh) return;
      // A glTF node with several primitives becomes a group of meshes, so
      // match the rules against the mesh and every node above it.
      const names: string[] = [];
      for (let n: THREE.Object3D | null = child; n; n = n.parent) {
        names.push(n.name);
      }
      // three.js sanitizes glTF node names on load ("Cone Normal" becomes
      // "Cone_Normal"); compare the rules the same way.
      const clean = THREE.PropertyBinding.sanitizeNodeName;
      const kept = spec.keep
        ? names.some((name) => spec.keep?.some((k) => clean(k) === name))
        : true;
      const dropped = names.some((name) =>
        spec.drop?.some((prefix) => name.startsWith(clean(prefix))),
      );
      if (!kept || dropped) remove.push(child);
      else child.castShadow = true;
    });
    for (const node of remove) node.removeFromParent();
    copy.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(copy);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    return {
      scene: copy,
      // Centre it over its spot and put its lowest point on the floor.
      offset: new THREE.Vector3(-center.x, -box.min.y, -center.z),
      height: size.y || 1,
      // Seen from an unknown angle: average the two horizontal extents.
      width: (size.x + size.z) / 2 || 1,
    };
  }, [gltf.scene, spec]);

  const target = fitHeight(spec, object, height, width);

  // Grow or shrink smoothly as the measurement settles, instead of popping.
  useFrame(() => {
    const g = sizer.current;
    if (!g) return;
    const scale = target / height;
    if (!g.userData.sized) {
      g.scale.setScalar(scale);
      g.userData.sized = true;
    } else {
      g.scale.setScalar(g.scale.x + (scale - g.scale.x) * SMOOTHING);
    }
  });

  const { actions, names } = useAnimations(gltf.animations, root);
  useEffect(() => {
    if (!names.length) return;
    const pick = (clip?: string) =>
      clip ? (actions[clip === "*" ? names[0] : clip] ?? null) : null;
    const walk = pick(spec.walk);
    const freeze = spec.still === "freeze";
    const still = freeze ? walk : pick(spec.still);
    let current: THREE.AnimationAction | null = null;
    const id = setInterval(() => {
      const isMoving = moving.current;
      const next = isMoving ? walk : still;
      if (next && next === walk) next.timeScale = isMoving || !freeze ? 1 : 0;
      if (next === current) return;
      current?.fadeOut(0.3);
      next?.reset().fadeIn(0.3).play();
      if (next && freeze && !isMoving)
        next.time = next.getClip().duration * 0.25;
      current = next;
    }, 200);
    return () => {
      clearInterval(id);
      current?.stop();
    };
  }, [actions, names, spec, moving]);

  return (
    <>
      {/* glTF is y-up; the parent scene is ROS z-up: stand the model upright. */}
      <group ref={sizer} rotation={[Math.PI / 2, 0, 0]}>
        <group ref={root}>
          <primitive object={scene} position={offset} />
        </group>
      </group>
      <Label object={object} height={target} />
    </>
  );
}

/** Fallback for labels without a model: a translucent, outlined block. */
function Block({ object }: { object: WorldObject }) {
  const height = clamp(object.height, 0.2, 2.2);
  const width = clamp(object.width, 0.2, 2.5);
  const depth = Math.min(width, 0.6);
  const color = colorFor(object.label);
  return (
    <mesh position={[0, 0, height / 2]} castShadow>
      <boxGeometry args={[depth, width, height]} />
      <meshStandardMaterial
        color={color}
        roughness={0.7}
        transparent
        opacity={0.35}
      />
      <Edges color={color} />
      <Label object={object} height={height / 2} />
    </mesh>
  );
}

/** One tracked object: glides to its latest position and faces the robot. */
function ObjectMarker({
  object,
  origin,
  robot,
}: {
  object: WorldObject;
  origin: React.RefObject<THREE.Vector3 | null>;
  robot: React.RefObject<THREE.Vector3>;
}) {
  const group = useRef<THREE.Group>(null);
  const moving = useRef(false);
  const spec = BY_LABEL.get(object.label.toLowerCase());

  useFrame((_, delta) => {
    const g = group.current;
    const o = origin.current;
    if (!g || !o) return;
    const x = object.x - o.x;
    const y = object.y - o.y;
    if (!g.userData.placed) {
      // First frame: snap into place instead of flying in from the origin.
      g.position.set(x, y, 0);
      g.userData.placed = true;
    } else {
      const before = g.position.clone();
      g.position.x += (x - g.position.x) * SMOOTHING;
      g.position.y += (y - g.position.y) * SMOOTHING;
      const speed = g.position.distanceTo(before) / Math.max(delta, 1e-3);
      moving.current = speed > WALKING_SPEED;
    }
    // Face the robot. A glTF model's front (+Z) ends up along -Y once stood
    // upright, hence the quarter turn.
    const r = robot.current;
    const target =
      Math.atan2(r.y - g.position.y, r.x - g.position.x) + Math.PI / 2;
    const turn = Math.atan2(
      Math.sin(target - g.rotation.z),
      Math.cos(target - g.rotation.z),
    );
    g.rotation.z += turn * SMOOTHING;
  });

  return (
    <group ref={group}>
      {spec ? (
        <Suspense fallback={<Block object={object} />}>
          <GltfModel spec={spec} object={object} moving={moving} />
        </Suspense>
      ) : (
        <Block object={object} />
      )}
    </group>
  );
}

/**
 * People and things the robot has detected, placed where it measured them,
 * drawn with real glTF models where one fits the label. Lives in the avatar's
 * ROS (z-up) group and shares its odometry origin.
 */
export function SemanticObjects({
  active,
  origin,
  robot,
  onCount,
}: {
  active: boolean;
  origin: React.RefObject<THREE.Vector3 | null>;
  /** The robot's position in the same (origin-shifted) frame. */
  robot: React.RefObject<THREE.Vector3>;
  onCount?: (count: number) => void;
}) {
  const [objects, setObjects] = useState<WorldObject[]>([]);

  useEffect(() => {
    if (!active) {
      setObjects([]);
      onCount?.(0);
      return;
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const world = await api.worldObjects();
        if (!stop) {
          setObjects(world.objects);
          onCount?.(world.objects.length);
        }
      } catch {
        /* backend busy or offline; keep the last picture */
      }
      if (!stop) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, 0);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [active, onCount]);

  return (
    <>
      {objects.map((object) => (
        <ObjectMarker
          key={object.id}
          object={object}
          origin={origin}
          robot={robot}
        />
      ))}
    </>
  );
}
