import uuid
import enum
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Text, Float, Integer, SmallInteger, String, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from geoalchemy2 import Geometry
from app.database import Base


class UserBehavior(str, enum.Enum):
    victim = "victim"
    observer = "observer"


class Report(Base):
    __tablename__ = "raw_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    report_id: Mapped[str] = mapped_column(nullable=False, unique=True)

    user_behavior: Mapped[str] = mapped_column(
        SAEnum(UserBehavior, name='user_behavior_type', create_type=False),
        nullable=False
    )

    can_communicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    gps_location = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=True)

    event_define: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_capture: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    image_location = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=True)
    image_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    report_date: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # İlişkiler
    exif = relationship("ExifFile", back_populates="report", uselist=False, cascade="all, delete-orphan")
    processed = relationship("ProcessedReport", back_populates="raw_report", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Report {self.report_id} {self.user_behavior}>"


class ProcessedReport(Base):
    __tablename__ = "processed_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    report_id: Mapped[str] = mapped_column(nullable=False, unique=True)
    raw_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_reports.id", ondelete="CASCADE"), nullable=True
    )

    user_behavior: Mapped[str] = mapped_column(
        SAEnum(UserBehavior, name='user_behavior_type', create_type=False),
        nullable=False
    )
    can_communicate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    gps_location = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=True)

    disaster_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    vlm_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    llm_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    indicators_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    location_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    user_location = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=True)
    image_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    report_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # İlişki
    raw_report = relationship("Report", back_populates="processed")

    def __repr__(self) -> str:
        return f"<ProcessedReport {self.report_id}>"


class DisasterCluster(Base):
    __tablename__ = "disaster_clusters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cluster_center = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=True)
    initial_center = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=True)
    radius_meters: Mapped[float | None] = mapped_column(Float, nullable=True, default=100.0)
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    severity_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_report_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_report_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    # İlişki
    cluster_reports = relationship("ClusterReport", back_populates="cluster", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<DisasterCluster {self.id} type={self.event_type} count={self.report_count}>"


class ClusterReport(Base):
    __tablename__ = "cluster_reports"

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("disaster_clusters.id", ondelete="CASCADE"), primary_key=True
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_reports.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )

    # İlişkiler
    cluster = relationship("DisasterCluster", back_populates="cluster_reports")
    report = relationship("Report")

    def __repr__(self) -> str:
        return f"<ClusterReport cluster={self.cluster_id} report={self.report_id}>"


class ExifFile(Base):
    __tablename__ = "exif_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_reports.id", ondelete="CASCADE"), nullable=True
    )

    date_taken: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_direction: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_speed: Mapped[float | None] = mapped_column(Float, nullable=True)

    device_make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orientation: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    flash_fired: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    iso: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brightness: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_exif: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # İlişki
    report = relationship("Report", back_populates="exif")

    def __repr__(self) -> str:
        return f"<ExifFile report={self.report_id}>"
