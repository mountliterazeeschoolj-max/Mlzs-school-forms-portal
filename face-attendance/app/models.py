"""Tables. staff_records belongs to the portal (read-only here); the face_* tables are created by the
Supabase migration `face_attendance_tables`."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

ZONES = ("CHECK_IN", "CHECK_OUT", "BOTH")


class StaffRecord(Base):
    """Portal staff master (read-only)."""
    __tablename__ = "staff_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    designation: Mapped[str | None] = mapped_column(Text)
    oasis_id: Mapped[str | None] = mapped_column(Text)
    employment_status: Mapped[str | None] = mapped_column(Text)
    embeddings: Mapped[list["FaceEmbedding"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class Camera(Base):
    __tablename__ = "face_cameras"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(500))      # RTSP URL or USB index like "0"
    zone: Mapped[str] = mapped_column(String(10), default="BOTH")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class FaceEmbedding(Base):
    """One 512-d float32 ArcFace vector per enrolled photo (raw bytes)."""
    __tablename__ = "face_embeddings"
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_records.id"), index=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Punch(Base):
    """Camera sightings: one IN per day, and one OUT per day that keeps moving to the latest sighting."""
    __tablename__ = "face_punches"
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("staff_records.id"), index=True)
    punched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(3))           # IN | OUT
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("face_cameras.id"), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
