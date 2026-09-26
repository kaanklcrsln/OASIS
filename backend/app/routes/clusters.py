import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2.shape import to_shape

from app.database import get_db
from app.models import DisasterCluster, ClusterReport, Report
from app.services.cluster_service import get_nearby_clusters, deactivate_stale_clusters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clusters", tags=["clusters"])


@router.get("/nearby")
async def nearby_clusters(
    lat: float = Query(..., description="Enlem"),
    lon: float = Query(..., description="Boylam"),
    radius: float = Query(5000, description="Arama yarıçapı (metre)"),
    db: AsyncSession = Depends(get_db),
):
    """Verilen koordinata yakın aktif afet kümelerini döner."""
    clusters = await get_nearby_clusters(lat, lon, radius, db)
    return {"clusters": clusters}


@router.get("/{cluster_id}")
async def get_cluster(cluster_id: str, db: AsyncSession = Depends(get_db)):
    """Cluster detayı — member report'lar dahil."""
    result = await db.execute(
        select(DisasterCluster).where(DisasterCluster.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Küme bulunamadı")

    # Cluster center
    try:
        center = to_shape(cluster.cluster_center)
        center_lat, center_lon = center.y, center.x
    except Exception:
        center_lat, center_lon = 0.0, 0.0

    # Member report'ları al
    members_q = await db.execute(
        select(Report)
        .join(ClusterReport, ClusterReport.report_id == Report.id)
        .where(ClusterReport.cluster_id == cluster.id)
        .order_by(Report.created_at.desc())
    )
    members = members_q.scalars().all()

    member_list = []
    for r in members:
        rlat, rlon = 0.0, 0.0
        if r.gps_location is not None:
            try:
                p = to_shape(r.gps_location)
                rlat, rlon = p.y, p.x
            except Exception:
                pass
        member_list.append({
            "id": str(r.id),
            "report_id": r.report_id,
            "latitude": rlat,
            "longitude": rlon,
            "event_define": r.event_define,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "id": str(cluster.id),
        "center_lat": center_lat,
        "center_lon": center_lon,
        "event_type": cluster.event_type,
        "report_count": cluster.report_count,
        "severity_avg": cluster.severity_avg,
        "radius_meters": cluster.radius_meters,
        "is_active": cluster.is_active,
        "is_locked": cluster.is_locked,
        "first_report_at": cluster.first_report_at.isoformat() if cluster.first_report_at else None,
        "last_report_at": cluster.last_report_at.isoformat() if cluster.last_report_at else None,
        "reports": member_list,
    }


@router.post("/cleanup", tags=["system"])
async def cleanup_stale(db: AsyncSession = Depends(get_db)):
    """24 saatten eski cluster'ları pasif yap."""
    count = await deactivate_stale_clusters(db)
    return {"deactivated": count}
