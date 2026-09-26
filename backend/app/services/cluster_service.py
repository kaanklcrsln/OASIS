"""
Cluster Servisi — Sabit Merkezli Büyüyen Buffer Algoritması
============================================================

Algoritma:
  1. Yeni rapor gelir → yakın aktif cluster aranır (arama yarıçapı = cluster.radius_meters)
  2. Uygun cluster bulunursa:
     a) Rapor cluster'a eklenir
     b) report_count güncellenir, severity_avg yeniden hesaplanır
     c) İLK 3 RAPOR (is_locked=False): cluster_center = ST_Centroid(tüm üyeler)
     d) 3. RAPOR SONRASI (is_locked=True):
        - cluster_center SABİT kalır (initial_center'dan kopyalanır)
        - Sadece radius_meters genişler (max 300m)
  3. Uygun cluster yoksa → yeni cluster: center = raporun konumu, radius = 100m

Sabitler:
  BUFFER_INITIAL  = 100m   (yeni cluster başlangıç yarıçapı)
  BUFFER_MAX      = 300m   (cluster buffer üst limiti)
  LOCK_THRESHOLD  = 3      (merkez kilitleme eşiği: 3 rapor)
  SEARCH_RADIUS   = 500m   (.env'den okunur, yakın cluster arama yarıçapı)
"""
import uuid
import logging
import math
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, text, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.shape import to_shape

from app.models import Report, ProcessedReport, DisasterCluster, ClusterReport
from app.config import settings

logger = logging.getLogger(__name__)

# ── Sabitler ──
SEARCH_RADIUS_M = settings.CLUSTER_RADIUS_METERS   # Yakın cluster arama yarıçapı (default 500m)
BUFFER_INITIAL  = 100.0    # Yeni cluster başlangıç yarıçapı (metre)
BUFFER_MAX      = 300.0    # Maksimum buffer yarıçapı (metre)
LOCK_THRESHOLD  = 3        # Bu sayıda rapor sonrası merkez kilitlenir
DBSCAN_EPS_DEG = 0.0005    # test script ile aynı: ~25-50m
DBSCAN_MIN_PTS = 3         # test script ile aynı


def _euclidean(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _region_query(points: list[tuple[float, float]], point_idx: int, eps: float) -> list[int]:
    neighbors: list[int] = []
    pivot = points[point_idx]
    for idx, point in enumerate(points):
        if _euclidean(pivot, point) < eps:
            neighbors.append(idx)
    return neighbors


def _expand_cluster(
    points: list[tuple[float, float]],
    labels: list[int],
    point_idx: int,
    cluster_id: int,
    eps: float,
    min_pts: int,
) -> bool:
    neighbors = _region_query(points, point_idx, eps)
    if len(neighbors) < min_pts:
        labels[point_idx] = -1
        return False

    labels[point_idx] = cluster_id
    queue = neighbors[:]
    head = 0

    while head < len(queue):
        current_point = queue[head]
        head += 1

        if labels[current_point] == -1:
            labels[current_point] = cluster_id
        elif labels[current_point] == 0:
            labels[current_point] = cluster_id
            new_neighbors = _region_query(points, current_point, eps)
            if len(new_neighbors) >= min_pts:
                queue.extend(new_neighbors)
    return True


def _dbscan(points: list[tuple[float, float]], eps: float, min_pts: int) -> list[int]:
    cluster_id = 0
    labels = [0] * len(points)

    for point_idx in range(len(points)):
        if labels[point_idx] == 0:
            if _expand_cluster(points, labels, point_idx, cluster_id + 1, eps, min_pts):
                cluster_id += 1
    return labels


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def rebuild_clusters_with_dbscan(db: AsyncSession) -> dict:
    """
    Tüm mevcut cluster yapısını siler ve raporları DBSCAN ile baştan kümeler.
    DBSCAN mantığı `ai_engine/dbscan.py` ile uyumludur (eps=0.0005, min_pts=3).
    """
    # 1) Cluster tablolarını temizle
    await db.execute(delete(ClusterReport))
    await db.execute(delete(DisasterCluster))
    await db.flush()

    # 2) İşlenmiş ve konumlu raporları çek
    rows = await db.execute(
        select(Report, ProcessedReport)
        .join(ProcessedReport, ProcessedReport.raw_report_id == Report.id)
        .where(Report.gps_location.isnot(None))
    )
    report_pairs = rows.all()

    if not report_pairs:
        await db.commit()
        return {
            "status": "no_reports",
            "total_reports": 0,
            "cluster_count": 0,
            "noise_singletons": 0,
        }

    grouped: dict[str, list[dict]] = {}
    for report, processed in report_pairs:
        if report.gps_location is None:
            continue
        try:
            point = to_shape(report.gps_location)
            lat, lon = float(point.y), float(point.x)
        except Exception:
            continue

        event_type = (processed.disaster_type or "unknown").strip().lower() or "unknown"
        grouped.setdefault(event_type, []).append({
            "report_id": report.id,
            "lat": lat,
            "lon": lon,
            "severity": processed.severity_score,
            "report_date": report.report_date,
            "created_at": report.created_at,
        })

    now = datetime.now(timezone.utc)
    cluster_count = 0
    assigned_reports = 0
    noise_singletons = 0

    for event_type, items in grouped.items():
        if not items:
            continue

        # Deterministik sıra: zaman + report_id
        items.sort(key=lambda item: (
            item["report_date"] or item["created_at"] or now,
            str(item["report_id"]),
        ))

        points = [(item["lon"], item["lat"]) for item in items]
        labels = _dbscan(points, DBSCAN_EPS_DEG, DBSCAN_MIN_PTS)

        clusters_by_label: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters_by_label.setdefault(label, []).append(idx)

        # Gürültü (-1) noktaları singleton cluster olarak tutulur
        next_label = max([label for label in clusters_by_label.keys() if label > 0], default=0) + 1
        if -1 in clusters_by_label:
            for noise_idx in clusters_by_label[-1]:
                clusters_by_label[next_label] = [noise_idx]
                next_label += 1
                noise_singletons += 1
            del clusters_by_label[-1]

        for member_indices in clusters_by_label.values():
            member_items = [items[i] for i in member_indices]
            member_count = len(member_items)
            if member_count == 0:
                continue

            # İlk merkez (initial_center): en erken raporun noktası
            first_item = member_items[0]
            initial_lat, initial_lon = first_item["lat"], first_item["lon"]

            # Cluster center: centroid (batch sonuç)
            center_lat = sum(item["lat"] for item in member_items) / member_count
            center_lon = sum(item["lon"] for item in member_items) / member_count

            # Radius: en uzak noktaya göre, min 100 max 300
            max_dist = 0.0
            for item in member_items:
                dist = _haversine_m(center_lat, center_lon, item["lat"], item["lon"])
                if dist > max_dist:
                    max_dist = dist
            radius = min(max(max_dist, BUFFER_INITIAL), BUFFER_MAX)

            # Severity ortalaması
            severity_values = [item["severity"] for item in member_items if item["severity"] is not None]
            severity_avg = round(sum(severity_values) / len(severity_values), 1) if severity_values else None

            first_report_at = min(
                (item["report_date"] or item["created_at"] or now) for item in member_items
            )
            last_report_at = max(
                (item["report_date"] or item["created_at"] or now) for item in member_items
            )

            is_locked = member_count >= LOCK_THRESHOLD

            cluster = DisasterCluster(
                cluster_center=f"SRID=4326;POINT({center_lon} {center_lat})",
                initial_center=f"SRID=4326;POINT({initial_lon} {initial_lat})",
                radius_meters=round(radius, 1),
                event_type=event_type,
                report_count=member_count,
                severity_avg=severity_avg,
                first_report_at=first_report_at,
                last_report_at=last_report_at,
                is_active=True,
                is_locked=is_locked,
                created_at=now,
                updated_at=now,
            )
            db.add(cluster)
            await db.flush()

            for item in member_items:
                db.add(ClusterReport(
                    cluster_id=cluster.id,
                    report_id=item["report_id"],
                    joined_at=now,
                ))

            cluster_count += 1
            assigned_reports += member_count

    await db.commit()

    return {
        "status": "done",
        "total_reports": len(report_pairs),
        "cluster_count": cluster_count,
        "assigned_reports": assigned_reports,
        "noise_singletons": noise_singletons,
        "eps": DBSCAN_EPS_DEG,
        "min_samples": DBSCAN_MIN_PTS,
    }


async def detect_and_assign_cluster(report_id: uuid.UUID, db: AsyncSession) -> dict | None:
    """
    Yeni report geldiğinde çağrılır.
    1. Report'un konumunu ve AI analizini al
    2. Yakın aktif cluster var mı bak (ST_DWithin — SEARCH_RADIUS_M)
    3. Aynı disaster_type + benzer severity → cluster'a ekle
    4. Yoksa → yeni cluster oluştur (radius=100m)
    5. 3 rapor sonrası merkez kilitlenir, sadece buffer genişler (max 300m)
    """

    # ── 1. Report'u al ──
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report or report.gps_location is None:
        logger.warning(f"Cluster: Report {report_id} bulunamadı veya konumu yok")
        return None

    try:
        point = to_shape(report.gps_location)
        lat, lon = point.y, point.x
    except Exception:
        logger.warning(f"Cluster: Report {report_id} konum parse edilemedi")
        return None

    # AI analiz sonucu
    proc_result = await db.execute(
        select(ProcessedReport).where(ProcessedReport.raw_report_id == report.id)
    )
    processed = proc_result.scalar_one_or_none()
    raw_event_type = processed.disaster_type if processed else None
    report_event_type = raw_event_type.strip().lower() if raw_event_type else None
    report_severity = processed.severity_score if processed else None

    # Only cluster flood-related reports
    _FLOOD_KEYWORDS = ['sel', 'taşkın', 'su baskın', 'flood']
    if not any(kw in (report_event_type or '') for kw in _FLOOD_KEYWORDS):
        logger.info(f"Cluster: Report {report_id} non-flood type '{report_event_type}', skipping cluster assignment")
        return None

    now = datetime.now(timezone.utc)

    # ── 2. Yakın aktif cluster'ları bul ──
    nearby_rows = await db.execute(
        text("""
            SELECT id FROM disaster_clusters
            WHERE is_active = TRUE
              AND ST_DWithin(
                  cluster_center::geography,
                  ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                  :radius
              )
            ORDER BY ST_Distance(
                cluster_center::geography,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
            )
        """),
        {"lon": lon, "lat": lat, "radius": SEARCH_RADIUS_M},
    )
    nearby_ids = [row[0] for row in nearby_rows.fetchall()]

    nearby_clusters = []
    for cid in nearby_ids:
        c_result = await db.execute(select(DisasterCluster).where(DisasterCluster.id == cid))
        c = c_result.scalar_one_or_none()
        if c:
            nearby_clusters.append(c)

    # ── 3. Aynı event_type'a sahip yakın cluster var mı? ──
    matched_cluster = None
    for cluster in nearby_clusters:
        cluster_et = cluster.event_type.strip().lower() if cluster.event_type else None
        if report_event_type is None or cluster_et is None or cluster_et == report_event_type:
            matched_cluster = cluster
            break

    if matched_cluster:
        # ════════════════════════════════════════════════
        # MEVCUT CLUSTER'A EKLE
        # ════════════════════════════════════════════════

        # Daha önce eklenmiş mi kontrol
        existing_link = await db.execute(
            select(ClusterReport).where(
                ClusterReport.cluster_id == matched_cluster.id,
                ClusterReport.report_id == report.id,
            )
        )
        if existing_link.scalar_one_or_none() is not None:
            # Zaten ekli — tekrar ekleme
            return {
                "cluster_id": str(matched_cluster.id),
                "event_type": matched_cluster.event_type,
                "report_count": matched_cluster.report_count,
                "severity_avg": matched_cluster.severity_avg,
                "is_locked": matched_cluster.is_locked,
                "message": f"Rapor zaten bu kümeye dahil",
            }

        # Raporu cluster'a ekle
        db.add(ClusterReport(
            cluster_id=matched_cluster.id,
            report_id=report.id,
            joined_at=now,
        ))

        # report_count güncelle
        count_q = await db.execute(
            text("SELECT COUNT(*) FROM cluster_reports WHERE cluster_id = :cid"),
            {"cid": str(matched_cluster.id)},
        )
        new_count = (count_q.scalar() or 0) + 1  # +1 çünkü flush henüz olmadı
        matched_cluster.report_count = new_count
        matched_cluster.last_report_at = now
        matched_cluster.updated_at = now

        if report_event_type and matched_cluster.event_type is None:
            matched_cluster.event_type = report_event_type

        # ── Severity ortalaması ──
        avg_q = await db.execute(
            text("""
                SELECT AVG(pr.severity_score)
                FROM cluster_reports cr
                JOIN processed_reports pr ON pr.raw_report_id = cr.report_id
                WHERE cr.cluster_id = :cid AND pr.severity_score IS NOT NULL
            """),
            {"cid": str(matched_cluster.id)},
        )
        avg_sev = avg_q.scalar()
        if avg_sev is not None:
            matched_cluster.severity_avg = round(float(avg_sev), 1)

        # ════════════════════════════════════════════════
        # MERKEZ KİLİTLEME KARARI
        # ════════════════════════════════════════════════

        if not matched_cluster.is_locked:
            # Henüz kilitlenmemiş — centroid yeniden hesapla
            centroid_q = await db.execute(
                text("""
                    SELECT ST_AsText(ST_Centroid(ST_Collect(r.gps_location)))
                    FROM cluster_reports cr
                    JOIN raw_reports r ON r.id = cr.report_id
                    WHERE cr.cluster_id = :cid
                """),
                {"cid": str(matched_cluster.id)},
            )
            centroid_wkt = centroid_q.scalar()
            if centroid_wkt:
                matched_cluster.cluster_center = f"SRID=4326;{centroid_wkt}"

            # 3. rapora ulaştıysa → kilitle
            if new_count >= LOCK_THRESHOLD:
                matched_cluster.is_locked = True
                # Kilitlenme anındaki centroid'i initial_center olarak kaydet
                matched_cluster.initial_center = matched_cluster.cluster_center
                logger.info(
                    f" Cluster {matched_cluster.id} kilitlendi — "
                    f"{new_count} rapor, merkez sabitlendi"
                )
        else:
            # Kilitli — merkez DEĞİŞMEZ, sadece buffer genişler
            pass

        # ════════════════════════════════════════════════
        # BUFFER YARICAP GÜNCELLEME (max 300m)
        # ════════════════════════════════════════════════

        # En uzak üyeye olan mesafe
        max_dist_q = await db.execute(
            text("""
                SELECT MAX(ST_Distance(
                    r.gps_location::geography,
                    dc.cluster_center::geography
                ))
                FROM cluster_reports cr
                JOIN raw_reports r ON r.id = cr.report_id
                JOIN disaster_clusters dc ON dc.id = cr.cluster_id
                WHERE cr.cluster_id = :cid
            """),
            {"cid": str(matched_cluster.id)},
        )
        max_dist = max_dist_q.scalar()

        if max_dist and max_dist > 0:
            # Buffer = en uzak üye mesafesi + %20 marj, ama min BUFFER_INITIAL, max BUFFER_MAX
            new_radius = min(max(float(max_dist) * 1.2, BUFFER_INITIAL), BUFFER_MAX)
            matched_cluster.radius_meters = round(new_radius, 1)
        else:
            # Tek nokta — başlangıç buffer'ı koru
            if matched_cluster.radius_meters is None or matched_cluster.radius_meters < BUFFER_INITIAL:
                matched_cluster.radius_meters = BUFFER_INITIAL

        await db.commit()

        logger.info(
            f"📌 Report {report.report_id} → cluster {matched_cluster.id} "
            f"(count={new_count}, locked={matched_cluster.is_locked}, "
            f"radius={matched_cluster.radius_meters}m, type={matched_cluster.event_type})"
        )

        return {
            "cluster_id": str(matched_cluster.id),
            "event_type": matched_cluster.event_type,
            "report_count": new_count,
            "severity_avg": matched_cluster.severity_avg,
            "radius_meters": matched_cluster.radius_meters,
            "is_locked": matched_cluster.is_locked,
            "message": f"Bu bölgede {new_count} afet bildirimi bulunmaktadır",
        }

    else:
        # ════════════════════════════════════════════════
        # YENİ CLUSTER OLUŞTUR
        # ════════════════════════════════════════════════

        wkt_point = f"SRID=4326;POINT({lon} {lat})"

        new_cluster = DisasterCluster(
            cluster_center=wkt_point,
            initial_center=wkt_point,          # İlk merkez kayıt altına alınır
            radius_meters=BUFFER_INITIAL,      # 100m başlangıç
            event_type=report_event_type,
            report_count=1,
            severity_avg=report_severity,
            first_report_at=now,
            last_report_at=now,
            is_active=True,
            is_locked=False,                   # Henüz kilitlenmemiş
        )
        db.add(new_cluster)
        await db.flush()

        db.add(ClusterReport(
            cluster_id=new_cluster.id,
            report_id=report.id,
            joined_at=now,
        ))
        await db.commit()

        logger.info(
            f"🆕 Yeni cluster oluşturuldu: {new_cluster.id} — "
            f"report={report.report_id}, type={report_event_type}, "
            f"radius={BUFFER_INITIAL}m"
        )
        return None


async def get_nearby_clusters(
    lat: float, lon: float, radius_m: float, db: AsyncSession
) -> list[dict]:
    """Verilen koordinata yakın aktif cluster'ları döner."""
    rows = await db.execute(
        text("""
            SELECT id, ST_Y(cluster_center) as lat, ST_X(cluster_center) as lon,
                   event_type, report_count, severity_avg, radius_meters,
                   first_report_at, last_report_at, is_locked
            FROM disaster_clusters
            WHERE is_active = TRUE
              AND ST_DWithin(
                  cluster_center::geography,
                  ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                  :radius
              )
            ORDER BY report_count DESC
        """),
        {"lon": lon, "lat": lat, "radius": radius_m},
    )

    out = []
    for r in rows.fetchall():
        out.append({
            "id": str(r[0]),
            "center_lat": r[1],
            "center_lon": r[2],
            "event_type": r[3],
            "report_count": r[4],
            "severity_avg": r[5],
            "radius_meters": r[6],
            "first_report_at": r[7].isoformat() if r[7] else None,
            "last_report_at": r[8].isoformat() if r[8] else None,
            "is_locked": r[9],
        })
    return out


async def deactivate_stale_clusters(db: AsyncSession, hours: int = 24) -> int:
    """24 saatten fazla güncellenmemiş cluster'ları pasif yap."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        update(DisasterCluster)
        .where(
            DisasterCluster.is_active == True,
            DisasterCluster.last_report_at < cutoff,
        )
        .values(is_active=False, updated_at=datetime.now(timezone.utc))
    )
    await db.commit()
    count = result.rowcount
    if count:
        logger.info(f"🕐 {count} eski cluster pasif yapıldı")
    return count
