"""FastAPI app: REST API, MJPEG streams and static dashboard (Supabase-backed)."""
import base64
import csv
import hashlib
import hmac
import io
import json
import secrets
import time
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Literal

import cv2
import numpy as np
from fastapi import Body, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .attendance import AttendanceService, query_rows
from .camera import CameraManager
from .config import (BASE_DIR, DEV_NO_AUTH, SECURE_COOKIES, SESSION_HOURS, SUPABASE_ANON_KEY,
                     SUPABASE_URL, TZ)
from .database import SessionLocal, check_db, get_db
from .face_engine import FaceEngine
from .models import Camera, FaceEmbedding, StaffRecord

service = AttendanceService()
engine: FaceEngine
manager: CameraManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, manager
    check_db()
    engine = FaceEngine()                                   # first run downloads the model (~280 MB)
    with SessionLocal() as db:
        engine.reload(db)
        manager = CameraManager(engine, service.handle)
        for cam in db.query(Camera).filter_by(enabled=True):
            manager.add(cam)
    yield
    manager.stop_all()


app = FastAPI(title="Face Attendance", lifespan=lifespan)

# ---- Login: only portal admins -------------------------------------------------------------
# The portal opens /sso#token=<Supabase access token>. We ask Supabase who that is and check admin_emails
# (the same list the portal uses), then issue our own signed session cookie.
COOKIE = "att_session"
_SESSION_KEY = secrets.token_bytes(32)                     # sessions end when the service restarts


def _sign(body: str) -> str:
    return hmac.new(_SESSION_KEY, body.encode(), hashlib.sha256).hexdigest()


def _make_session(email: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"e": email, "x": int(time.time()) + SESSION_HOURS * 3600}).encode()).decode()
    return f"{body}.{_sign(body)}"


def _session_ok(cookie: str | None) -> bool:
    try:
        body, sig = (cookie or "").split(".", 1)
        return hmac.compare_digest(sig, _sign(body)) and json.loads(base64.urlsafe_b64decode(body))["x"] > time.time()
    except Exception:
        return False


def _supabase_email(token: str) -> str | None:
    req = urllib.request.Request(f"{SUPABASE_URL}/auth/v1/user",
                                 headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return (json.load(r).get("email") or "").lower() or None
    except Exception:
        return None


@app.middleware("http")
async def auth_and_headers(request, call_next):
    open_paths = ("/sso", "/auth/session", "/healthz")
    if request.url.path not in open_paths and not DEV_NO_AUTH and not _session_ok(request.cookies.get(COOKIE)):
        resp = Response("Please open Face Attendance from the school portal (Attendance > Face Attendance).",
                        status_code=401)
    else:
        resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


SSO_PAGE = """<!doctype html><meta charset=utf-8><title>Signing in</title>
<p style="font:16px system-ui;padding:2rem" id=m>Signing in...</p>
<script>
const t = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", "/sso");
fetch("/auth/session", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: t})})
  .then(r => r.ok ? location.replace("/") : r.json().then(e => { m.textContent = e.detail || "Sign-in failed"; }))
  .catch(() => { m.textContent = "Sign-in failed"; });
</script>"""


@app.get("/sso", response_class=HTMLResponse)
def sso_page():
    return SSO_PAGE


@app.post("/auth/session")
def create_session(body: dict = Body(...), db: Session = Depends(get_db)):
    token = body.get("token")
    email = _supabase_email(token) if token and SUPABASE_URL else None
    if not email:
        raise HTTPException(401, "Your portal session could not be verified. Sign in to the portal again.")
    is_admin = db.execute(text("select 1 from public.admin_emails where lower(email) = :e and role = 'ADMIN'"),
                          {"e": email}).first()
    if not is_admin:
        raise HTTPException(403, "Only portal administrators can open Face Attendance.")
    resp = Response('{"ok": true}', media_type="application/json")
    resp.set_cookie(COOKIE, _make_session(email), max_age=SESSION_HOURS * 3600, httponly=True,
                    secure=SECURE_COOKIES, samesite="lax")
    return resp


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


# ------------------------------------------------------------------ cameras
class CameraIn(BaseModel):
    name: str
    source: str                                             # rtsp://user:pass@ip:554/stream  or  0
    zone: Literal["CHECK_IN", "CHECK_OUT", "BOTH"] = "BOTH"


def _cam_dict(c: Camera):
    s = manager.get(c.id)
    return {"id": c.id, "name": c.name, "source": c.source, "zone": c.zone,
            "connected": bool(s and s.connected)}


@app.get("/api/cameras")
def list_cameras(db: Session = Depends(get_db)):
    return [_cam_dict(c) for c in db.query(Camera).all()]


@app.post("/api/cameras")
def add_camera(body: CameraIn, db: Session = Depends(get_db)):
    cam = Camera(**body.model_dump())
    db.add(cam)
    db.commit()
    manager.add(cam)
    return _cam_dict(cam)


@app.delete("/api/cameras/{cam_id}")
def delete_camera(cam_id: int, db: Session = Depends(get_db)):
    cam = db.get(Camera, cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    manager.remove(cam_id)
    db.delete(cam)
    db.commit()
    return {"ok": True}


@app.get("/api/cameras/{cam_id}/stream")
def stream(cam_id: int):
    cam = manager.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not running")

    def frames():
        while cam.running:
            jpg = cam.jpeg()
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.06)                                # ~15 fps to the browser

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


# -------------------------------------------------------------------- staff
# Staff are created and managed in the portal (staff_records). Here we only enrol their faces.
def _embed(img) -> np.ndarray | None:
    """Embedding of the largest face in an image, or None if no face is found."""
    faces = engine.analyze(img)
    if not faces:
        return None
    f = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return f.normed_embedding.astype(np.float32)


def _get_staff(db: Session, staff_id: str) -> StaffRecord:
    try:
        staff = db.get(StaffRecord, uuid.UUID(staff_id))
    except ValueError:
        staff = None
    if not staff or staff.employment_status != "active":
        raise HTTPException(404, "Staff member not found")
    return staff


@app.get("/api/staff")
def list_staff(db: Session = Depends(get_db)):
    return [{"id": str(s.id), "emp_code": s.oasis_id or "", "full_name": s.full_name,
             "department": s.designation or "", "photos": len(s.embeddings)}
            for s in db.query(StaffRecord).filter_by(employment_status="active").order_by(StaffRecord.full_name)]


@app.post("/api/staff/{staff_id}/photos")
def add_photos(staff_id: str, photos: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    staff = _get_staff(db, staff_id)
    ok = bad = 0
    for f in photos:
        img = cv2.imdecode(np.frombuffer(f.file.read(), np.uint8), cv2.IMREAD_COLOR)
        emb = _embed(img) if img is not None else None
        if emb is None:
            bad += 1
            continue
        db.add(FaceEmbedding(staff_id=staff.id, vector=emb.tobytes()))
        ok += 1
    db.commit()
    engine.reload(db)
    return {"photos_ok": ok, "photos_without_face": bad}


@app.post("/api/staff/{staff_id}/capture/{cam_id}")
def capture(staff_id: str, cam_id: int, db: Session = Depends(get_db)):
    """Enrol a face straight from a live camera frame (exactly one face must be visible)."""
    staff, cam = _get_staff(db, staff_id), manager.get(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    frame = cam.snapshot()
    if frame is None:
        raise HTTPException(409, "Camera has no frame yet")
    faces = engine.analyze(frame)
    if len(faces) != 1:
        raise HTTPException(422, f"Expected exactly 1 face, found {len(faces)}")
    db.add(FaceEmbedding(staff_id=staff.id, vector=faces[0].normed_embedding.astype(np.float32).tobytes()))
    db.commit()
    engine.reload(db)
    return {"photos": len(staff.embeddings)}


@app.delete("/api/staff/{staff_id}/faces")
def delete_faces(staff_id: str, db: Session = Depends(get_db)):
    """Removes this person's face data only. The staff record and attendance history are untouched."""
    staff = _get_staff(db, staff_id)
    staff.embeddings.clear()
    db.commit()
    engine.reload(db)
    return {"ok": True}


# --------------------------------------------------------------- attendance
@app.get("/api/attendance")
def attendance(day: date | None = None, department: str | None = None, db: Session = Depends(get_db)):
    return query_rows(db, day or datetime.now(TZ).date(), department or None)


@app.get("/api/departments")
def departments(db: Session = Depends(get_db)):
    return sorted({d for (d,) in db.query(StaffRecord.designation).filter_by(employment_status="active") if d})


@app.get("/api/attendance/export.csv")
def export_csv(day: date | None = None, department: str | None = None, db: Session = Depends(get_db)):
    day = day or datetime.now(TZ).date()
    rows = query_rows(db, day, department or None)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Staff ID", "Name", "Designation", "Date", "Check-In", "Check-Out", "Total Hours", "Status"])
    for r in rows:
        w.writerow([r["emp_code"], r["name"], r["department"], r["date"], r["check_in"],
                    r["check_out"], r["total_hours"], r["status"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="attendance_{day}.csv"'})
