from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class ReportCreate(BaseModel):
    report_id: str = Field(..., description="RPT-YYYYMMDD-XXXX")
    user_behavior: str = Field(..., description="'victim' veya 'observer'")
    can_communicate: bool = Field(..., description="Kullanici yazi yazabilir mi?")
    latitude: float = Field(..., description="GPS enlemi")
    longitude: float = Field(..., description="GPS boylami")
    event_define: str | None = Field(default=None, description="Durum aciklamasi")
    event_capture: str | None = Field(default=None, description="Fotograf URI")
    report_date: str = Field(..., description="ISO zaman damgasi")

    model_config = {
        "json_schema_extra": {
            "example": {
                "report_id": "RPT-20260220-4821",
                "user_behavior": "observer",
                "can_communicate": True,
                "latitude": 39.9334,
                "longitude": 32.8597,
                "event_define": "Bina coktu, altinda insanlar var",
                "event_capture": None,
                "report_date": "2026-02-20T14:30:00.000Z"
            }
        }
    }


class ReportResponse(BaseModel):
    id: str
    report_id: str
    user_behavior: str
    can_communicate: bool
    latitude: float
    longitude: float
    event_define: str | None
    event_capture: str | None
    image_path: str | None = None
    report_date: str
    created_at: datetime
    nearby_cluster: Optional[dict] = None
    # AI-enriched fields (from processed_reports)
    severity_score: float | None = None
    processed_summary: str | None = None
    processed_user_status: str | None = None
    processed_disaster_type: str | None = None
    processed_location_match_score: float | None = None
    # Indicator-based scoring fields
    indicators: Optional[dict] = None
    category_scores: Optional[dict] = None
    active_indicators: Optional[list] = None
    reliability_score: float | None = None
    formula_breakdown: str | None = None


class ReportListResponse(BaseModel):
    total: int
    reports: list[ReportResponse]


class ProcessedReportResponse(BaseModel):
    id: str
    report_id: str
    raw_report_id: str | None
    user_behavior: str
    user_status: str | None
    disaster_type: str | None
    severity_score: float | None
    llm_analysis: dict | None
    vlm_analysis: dict | None
    indicators_json: dict | None
    location_match_score: float | None
    processed_at: datetime | None


class NearbyClusterResponse(BaseModel):
    cluster_id: str
    event_type: str | None
    report_count: int
    severity_avg: float | None
    radius_meters: float | None = None
    is_locked: bool = False
    message: str


class ClusterResponse(BaseModel):
    id: str
    center_lat: float
    center_lon: float
    event_type: str | None
    report_count: int
    severity_avg: float | None
    radius_meters: float | None
    is_locked: bool = False
    first_report_at: str | None
    last_report_at: str | None


class ClusterListResponse(BaseModel):
    clusters: list[ClusterResponse]


class ExifUpload(BaseModel):
    """Mobil app'ten gelen EXIF verisi (fotoğraf çekiminde cihaz tarafından ayrıştırılmış)."""
    date_taken: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    gps_direction: Optional[float] = None
    gps_speed: Optional[float] = None
    device_make: Optional[str] = None
    device_model: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    orientation: Optional[int] = None
    flash_fired: Optional[bool] = None
    iso: Optional[int] = None
    brightness: Optional[float] = None
    raw_exif: Optional[dict] = None


class ExifResponse(BaseModel):
    id: int
    report_id: str | None
    date_taken: datetime | None
    latitude: float | None
    longitude: float | None
    altitude: float | None
    device_make: str | None
    device_model: str | None
    image_width: int | None
    image_height: int | None
    orientation: int | None
    flash_fired: bool | None
    iso: int | None
    brightness: float | None
