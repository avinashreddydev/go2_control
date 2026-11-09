""" YOLOE-based object detection for the robot camera feed """

import logging
import threading

MIN_CONFIDENCE = 0.55
WEIGHTS_PATH = "weights/yoloe-26s-seg-pf.pt"

log = logging.getLogger(__name__)


class YoloeDetector:
    """
    Runs YOLOE segmentation/detection.

    Torch, ultralytics, and the weights file are optional: on a machine without
    them the detector reports unavailable and every call becomes a no-op, so
    teleop, the video feed, and the VLA loop still run.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.model = self._load_model()
        self.active = self.model is not None
        self._labels: list[str] | None = None

    @staticmethod
    def _load_model():
        """ Load YOLOE, or return None when the vision stack is unavailable """
        try:
            from ultralytics import YOLOE

            return YOLOE(WEIGHTS_PATH)
        except Exception as exc:
            log.warning("YOLOE unavailable (%s: %s); detection disabled", type(exc).__name__, exc)
            return None

    @property
    def available(self) -> bool:
        return self.model is not None

    @staticmethod
    def _device():
        try:
            import torch

            return 1 if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    # ----- Label config -----
    def set_labels(self, labels: list[str]) -> None:
        """ Restrict detection to these class names (empty list clears the filter) """
        self._labels = labels

    def get_labels(self) -> list[str]:
        """ Return the active label filter, or [] when unset """
        return self._labels or []

    def _class_ids(self) -> list[int] | None:
        """ Map configured label names to YOLOE class indices, or None for all classes """
        if not self._labels or self.model is None:
            return None
        name_to_id = {name: idx for idx, name in self.model.names.items()}
        return [name_to_id[label] for label in self._labels if label in name_to_id]


    # ----- Inference -----
    def _predict(self, img):
        """ Run a single forward pass; returns the first (only) Results object """
        if self.model is None:
            return None
        with self._lock:
            return self.model.predict(
                img,
                conf=MIN_CONFIDENCE,
                device=self._device(),
                verbose=False,
                classes=self._class_ids(),
            )[0]

    def _labels_from(self, result) -> set[str]:
        """ Extract unique detected class names from a YOLOE Results object """
        if result is None or result.boxes is None:
            return set()
        return {result.names[int(cls)] for cls in result.boxes.cls}

    def locate(self, img, label: str) -> tuple[bool, float | None]:
        """
        Check whether `label` appears in `img` and return its horizontal center.

        Returns (visible, center_x) where center_x is normalized to [0, 1]
        (0 = left edge, 0.5 = frame center, 1 = right edge). center_x is None
        when the label is not detected.
        """
        result = self._predict(img)
        if result is None or result.boxes is None or len(result.boxes) == 0:
            return False, None

        _, width = img.shape[:2]
        best_conf = -1.0
        best_center_x: float | None = None

        for box in result.boxes:
            if result.names[int(box.cls)] != label:
                continue
            conf = float(box.conf)
            if conf <= best_conf:
                continue
            x1, _, x2, _ = box.xyxy[0].tolist()
            best_conf = conf
            best_center_x = ((x1 + x2) / 2.0) / width

        if best_center_x is None:
            return False, None
        return True, best_center_x


    # ----- Public API -----
    def predict(self, img):
        """ Run detection and return a BGR frame with bounding boxes drawn """
        result = self._predict(img)
        return img if result is None else result.plot(masks=False)

    def detect(self, img) -> set[str]:
        """ Run detection and return only the set of detected label names """
        return self._labels_from(self._predict(img))
