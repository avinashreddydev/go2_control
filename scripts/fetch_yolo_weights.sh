#!/usr/bin/env bash
#
# Download the YOLOE prompt-free weights the backend uses for detection
# (weights/yoloe-26s-seg-pf.pt, ~40 MB, from Ultralytics' releases).
# Needs the optional vision deps: uv pip install -r requirements-vision.txt
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p weights
cd weights
../.venv/bin/python -c "from ultralytics import YOLOE; YOLOE('yoloe-26s-seg-pf.pt'); print('weights/yoloe-26s-seg-pf.pt ready')"
