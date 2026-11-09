#!/usr/bin/env bash
#
# Download glTF models used to draw detected objects in the 3D view.
# Source: github.com/KhronosGroup/glTF-Sample-Assets (each model CC0-1.0 or
# CC-BY-4.0; see frontend/public/models/CREDITS.md). Saved to
# frontend/public/models/, which is git-ignored (~30 MB).
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models"
DEST="frontend/public/models"
MODELS=(CesiumMan SheenChair GlamVelvetSofa DiffuseTransmissionPlant GlassVaseFlowers
        DiffuseTransmissionTeacup WaterBottle TrafficCone Fox)

mkdir -p "$DEST"
{
    echo "# 3D object models"
    echo
    echo "From the Khronos glTF Sample Assets (https://github.com/KhronosGroup/glTF-Sample-Assets)."
    echo "Each model keeps its own license and attribution, listed below."
    echo
} > "$DEST/CREDITS.md"

for m in "${MODELS[@]}"; do
    [ -s "$DEST/$m.glb" ] || curl -fsSL "$BASE/$m/glTF-Binary/$m.glb" -o "$DEST/$m.glb"
    curl -fsSL "$BASE/$m/metadata.json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'## $m')
for l in d.get('legal', []):
    who = l.get('artist') or l.get('owner') or ''
    print(f\"- {l.get('license', '?')}: {l.get('text', '').strip()} {('(' + who + ')') if who else ''}\".rstrip())
print()
" >> "$DEST/CREDITS.md"
done
echo "Models in $DEST"
