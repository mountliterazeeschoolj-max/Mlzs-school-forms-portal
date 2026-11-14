"""Attendance rules. Punch times live in face_punches; the day's PRESENT/LATE status is written into the
portal's att_employee_attendance by the database function face_mark_attendance (uses att_settings)."""
import threading
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from .config import COOLDOWN_SECONDS, TZ
from .database import SessionLocal
from .models import Punch, StaffRecord


def _day_bounds(day):
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return start, start + timedelta(days=1)


class AttendanceService:
    def __init__(self):
        self._last: dict[tuple[str, int], datetime] = {}   # (staff, camera) -> last handled time
        self._lock = threading.Lock()

    def handle(self, staff_id: str, cam_id: int, zone: str, score: float | None = None,
               now: datetime | None = None) -> str | None:
        """Called for every live, recognised face. Returns 'check_in', 'check_out' or None."""
        now = now or datetime.now(TZ)
        cooldown = timedelta(seconds=COOLDOWN_SECONDS)
        with self._lock:                                    # anti-duplicate: same person + same camera
            if now - self._last.get((staff_id, cam_id), datetime.min.replace(tzinfo=TZ)) < cooldown:
                return None
            self._last[(staff_id, cam_id)] = now

        start, end = _day_bounds(now.date())
        sid = uuid.UUID(staff_id)
        with SessionLocal() as db:
            staff = db.get(StaffRecord, sid)
            if staff is None or staff.employment_status != "active":
                return None
            punches = (db.query(Punch).filter(Punch.staff_id == sid, Punch.punched_at >= start,
                                              Punch.punched_at < end).all())
            first_in = next((p for p in punches if p.kind == "IN"), None)
            out = next((p for p in punches if p.kind == "OUT"), None)

            if first_in is None and zone in ("CHECK_IN", "BOTH"):
                db.add(Punch(staff_id=sid, punched_at=now, kind="IN", camera_id=cam_id, score=score))
                db.execute(text("select public.face_mark_attendance(cast(:s as uuid), :t)"), {"s": staff_id, "t": now})
                db.commit()
                return "check_in"

            if first_in is not None and zone in ("CHECK_OUT", "BOTH"):
                if now - first_in.punched_at < cooldown:    # don't check out right after checking in
                    return None
                if out is None:
                    db.add(Punch(staff_id=sid, punched_at=now, kind="OUT", camera_id=cam_id, score=score))
                else:
                    out.punched_at, out.camera_id, out.score = now, cam_id, score   # later sightings extend check-out
                db.commit()
                return "check_out"
        return None


def query_rows(db, day, designation: str | None = None) -> list[dict]:
    """One row per staff member seen on `day`, optionally filtered by designation."""
    start, end = _day_bounds(day)
    q = (db.query(Punch, StaffRecord).join(StaffRecord, Punch.staff_id == StaffRecord.id)
         .filter(Punch.punched_at >= start, Punch.punched_at < end))
    if designation:
        q = q.filter(StaffRecord.designation == designation)
    per: dict = {}
    for p, s in q.order_by(Punch.punched_at).all():
        row = per.setdefault(s.id, {"staff": s, "in": None, "out": None})
        row["in" if p.kind == "IN" else "out"] = p.punched_at
    status = {str(r[0]): r[1] for r in db.execute(
        text("select teacher_id, status from public.att_employee_attendance where attendance_date = :d"), {"d": day})}
    rows = []
    for sid, r in sorted(per.items(), key=lambda kv: kv[1]["in"] or kv[1]["out"]):
        s, cin, cout = r["staff"], r["in"], r["out"]
        late = status.get(str(sid)) == "LATE"
        hours = round((cout - cin).total_seconds() / 3600, 2) if cin and cout else None
        rows.append({"emp_code": s.oasis_id or "", "name": s.full_name, "department": s.designation or "",
                     "date": day.isoformat(),
                     "check_in": cin.astimezone(TZ).strftime("%H:%M:%S") if cin else "",
                     "check_out": cout.astimezone(TZ).strftime("%H:%M:%S") if cout else "",
                     "total_hours": hours if hours is not None else "",
                     "status": "CHECKED_OUT" if cout else ("LATE" if late else "ON_TIME"), "was_late": late})
    return rows
