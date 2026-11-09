#!/usr/bin/env bash
#
# Download the official Unitree Go2 model (URDF + COLLADA meshes) for the 3D
# avatar. Source: github.com/unitreerobotics/unitree_ros, robots/go2_description
# (BSD-3-Clause). Saved to frontend/public/go2/, which is git-ignored (~26 MB).
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="https://raw.githubusercontent.com/unitreerobotics/unitree_ros/master/robots/go2_description"
DEST="frontend/public/go2"
MESHES=(base.dae hip.dae thigh.dae thigh_mirror.dae calf.dae calf_mirror.dae foot.dae)

mkdir -p "$DEST/dae" "$DEST/urdf"
curl -fsSL "$BASE/urdf/go2_description.urdf" -o "$DEST/urdf/go2_description.urdf"
for mesh in "${MESHES[@]}"; do
    [ -s "$DEST/dae/$mesh" ] || curl -fsSL "$BASE/dae/$mesh" -o "$DEST/dae/$mesh"
done
curl -fsSL "https://raw.githubusercontent.com/unitreerobotics/unitree_ros/master/LICENSE" -o "$DEST/LICENSE" || true
echo "Go2 model in $DEST"
