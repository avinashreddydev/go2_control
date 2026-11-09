import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import cv2
import numpy as np
import time

height, width = 720, 1280
img = np.zeros((height, width, 3), dtype=np.uint8)
cv2.imshow('Video', img)
cv2.waitKey(1)

from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from backend.services.robot import RobotClient, TELEOP_KEYS
from backend.services.vision import YoloeDetector



def handle_key(key_uni, robot, vision):
    key = chr(key_uni)

    if key == 'q':
        return False
    if key == 'y':
        vision.active = not vision.active
    if key in TELEOP_KEYS:
        robot.controller(key)
    
    return True

async def main():
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP)
    robot = RobotClient(conn)
    vision = YoloeDetector()

    try:
        robot.start()

        while True:
            active = handle_key(cv2.waitKey(1) & 0xFF, robot, vision)

            if not active:
                break
            
            # Receive frames
            if not robot.frame_queue.empty():
                img = robot.frame_queue.get()
                
                # Convert to YOLOE if active
                if vision.active:
                    img = vision.predict(img)

                cv2.imshow('Video', img)
                
            else:
                time.sleep(0.01)
    finally:
        cv2.destroyAllWindows()
        robot.stop()


if __name__ == "__main__":
    asyncio.run(main())