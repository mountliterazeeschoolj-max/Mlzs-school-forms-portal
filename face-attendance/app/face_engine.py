"""InsightFace wrapper: detection, embedding, matching and a basic liveness check."""
import threading

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from .config import (DET_SIZE, LIVENESS_ENABLED, LIVENESS_MIN_SHARPNESS,
                     MATCH_THRESHOLD, MIN_FACE_PX, MODEL_NAME)
from .models import FaceEmbedding, StaffRecord


class FaceEngine:
    def __init__(self):
        # CPU by default. For NVIDIA GPU: pip install onnxruntime-gpu and add "CUDAExecutionProvider".
        # Only load detection + recognition. The default also runs 3D/2D landmark and age/gender
        # models on every face, which we never use and which roughly doubles CPU time.
        self.app = FaceAnalysis(name=MODEL_NAME, allowed_modules=["detection", "recognition"],
                                providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=(DET_SIZE, DET_SIZE))
        self._lock = threading.Lock()          # one inference at a time across camera threads
        self._ids: list[str] = []
        self._mat = np.empty((0, 512), dtype=np.float32)
        self._names: dict[str, str] = {}

    # ---- gallery -----------------------------------------------------------
    def reload(self, db):
        """Rebuild the in-memory matrix from the DB. Call after any enrollment change."""
        rows = (db.query(FaceEmbedding, StaffRecord).join(StaffRecord, FaceEmbedding.staff_id == StaffRecord.id)
                .filter(StaffRecord.employment_status == "active").all())
        names = {str(e.id): e.full_name for _, e in rows}
        new_ids = [str(e.id) for _, e in rows]
        new_mat = (np.vstack([np.frombuffer(f.vector, dtype=np.float32) for f, _ in rows])
                   if rows else np.empty((0, 512), dtype=np.float32))
        self._names, self._ids, self._mat = names, new_ids, new_mat

    def match(self, emb: np.ndarray):
        """Return (staff_id | None, best cosine similarity)."""
        if not self._ids:
            return None, 0.0
        sims = self._mat @ emb                   # embeddings are L2-normalised -> dot = cosine
        i = int(np.argmax(sims))
        score = float(sims[i])
        return (self._ids[i] if score >= MATCH_THRESHOLD else None), score

    # ---- inference ---------------------------------------------------------
    def analyze(self, frame):
        with self._lock:
            return self.app.get(frame)

    @staticmethod
    def is_live(frame, bbox) -> bool:
        """Basic passive anti-spoofing: texture sharpness of the face crop.
        Flat prints and low-res screen replays tend to be blurrier than real skin.
        NOT sufficient against good attacks - use IR/depth hardware or a trained PAD model for high security."""
        x1, y1, x2, y2 = [max(0, int(v)) for v in bbox]
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False
        gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
        return cv2.Laplacian(gray, cv2.CV_64F).var() >= LIVENESS_MIN_SHARPNESS

    def recognize(self, frame) -> list[dict]:
        out = []
        for f in self.analyze(frame):
            x1, y1, x2, y2 = [int(v) for v in f.bbox]
            if x2 - x1 < MIN_FACE_PX:
                continue
            live = self.is_live(frame, f.bbox) if LIVENESS_ENABLED else True
            emp_id, score = self.match(f.normed_embedding)
            out.append({"bbox": (x1, y1, x2, y2), "emp_id": emp_id, "score": score, "live": live,
                        "name": self._names.get(emp_id, "Unknown")})
        return out
