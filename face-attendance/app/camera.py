"""Threaded camera capture + recognition. One grabber thread and one AI thread per camera,
so slow inference never stalls frame reading (no buffered/laggy RTSP video)."""
import os
import threading
import time

# Must be set before OpenCV opens a stream: TCP is far more reliable than UDP for CCTV.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer")
import cv2  # noqa: E402

from .config import PROCESS_FPS, PROCESS_MAX_WIDTH, STREAM_MAX_WIDTH  # noqa: E402

GREEN, ORANGE, RED = (60, 180, 75), (0, 140, 255), (50, 50, 220)


class CameraStream:
    def __init__(self, cam_id, name, source, zone, engine, on_recognition):
        self.cam_id, self.name, self.zone = cam_id, name, zone
        self.source = int(source) if str(source).isdigit() else source   # "0" -> USB index
        self.engine, self.on_recognition = engine, on_recognition
        self.frame = None
        self.frame_no = 0
        self.detections: list[dict] = []
        self.det_time = 0.0                  # when detections were last updated
        self.connected = False
        self.running = False
        self._lock = threading.Lock()

    def start(self):
        self.running = True
        threading.Thread(target=self._grab_loop, daemon=True).start()
        threading.Thread(target=self._ai_loop, daemon=True).start()

    def stop(self):
        self.running = False

    # ---- thread 1: read frames as fast as the camera delivers, keep only the newest ----
    def _grab_loop(self):
        cap = None
        while self.running:
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(self.source)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    self.connected = False
                    time.sleep(3)                       # auto-reconnect
                    continue
            ok, frame = cap.read()
            if not ok:
                self.connected = False
                cap.release()
                cap = None
                time.sleep(1)
                continue
            self.connected = True
            with self._lock:
                self.frame, self.frame_no = frame, self.frame_no + 1
        if cap:
            cap.release()

    # ---- thread 2: recognise at PROCESS_FPS on a downscaled copy ----
    def _ai_loop(self):
        last_no, interval = -1, 1.0 / PROCESS_FPS
        while self.running:
            t0 = time.time()
            with self._lock:
                frame, no = self.frame, self.frame_no
            if frame is None or no == last_no:
                time.sleep(0.05)
                continue
            last_no = no
            scale = min(1.0, PROCESS_MAX_WIDTH / frame.shape[1])
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
            try:
                results = self.engine.recognize(small)
            except Exception as exc:                    # never let one bad frame kill the thread
                print(f"[{self.name}] recognition error: {exc}")
                continue
            for r in results:
                r["bbox"] = tuple(int(v / scale) for v in r["bbox"])   # back to full-res coords
                if r["emp_id"] and r["live"]:
                    self.on_recognition(r["emp_id"], self.cam_id, self.zone, r["score"])
            self.detections = results
            self.det_time = time.time()
            time.sleep(max(0.0, interval - (time.time() - t0)))

    # ---- consumers ----
    def snapshot(self):
        with self._lock:
            return None if self.frame is None else self.frame.copy()

    def jpeg(self) -> bytes | None:
        """Latest frame with boxes + labels drawn on, JPEG-encoded for the MJPEG stream."""
        frame = self.snapshot()
        if frame is None:
            return None
        # Shrink before drawing/encoding: JPEG-encoding full 1080p/4K frames is a big CPU cost.
        scale = min(1.0, STREAM_MAX_WIDTH / frame.shape[1])
        if scale < 1:
            frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        fresh = time.time() - self.det_time < 1.5        # hide boxes that are out of date
        for d in (self.detections if fresh else []):
            x1, y1, x2, y2 = [int(v * scale) for v in d["bbox"]]
            if not d["live"]:
                color, label = RED, "Spoof suspected"
            elif d["emp_id"]:
                color, label = GREEN, f'{d["name"]} {d["score"]:.2f}'
            else:
                color, label = ORANGE, f'Unknown {d["score"]:.2f}'
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, label, (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None


class CameraManager:
    def __init__(self, engine, on_recognition):
        self.engine, self.on_recognition = engine, on_recognition
        self.streams: dict[int, CameraStream] = {}

    def add(self, cam):
        s = CameraStream(cam.id, cam.name, cam.source, cam.zone, self.engine, self.on_recognition)
        s.start()
        self.streams[cam.id] = s

    def remove(self, cam_id):
        s = self.streams.pop(cam_id, None)
        if s:
            s.stop()

    def get(self, cam_id):
        return self.streams.get(cam_id)

    def stop_all(self):
        for s in self.streams.values():
            s.stop()
