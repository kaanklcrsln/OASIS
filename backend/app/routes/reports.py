import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from geoalchemy2.shape import to_shape

from app.config import settings
from app.database import get_db, AsyncSessionLocal
from app.models import Report, ExifFile, ProcessedReport
from app.schemas import ReportCreate, ReportResponse, ReportListResponse, ExifUpload
from app.services.exif_service import extract_exif
from app.services.image_service import save_original_temp, compress_and_save, get_relative_path
from app.services.ai_service import analyze_report
from app.services.cluster_service import detect_and_assign_cluster, rebuild_clusters_with_dbscan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])

_batch_running = False

# Test verisi görsellerinin IMAGES_DIR altındaki klasörü
SAMPLE_IMAGES_SUBDIR = "samples"


def _to_response(r: Report) -> dict:
    lat, lng = 0.0, 0.0
    if r.gps_location is not None:
        try:
            point = to_shape(r.gps_location)
            lat, lng = point.y, point.x
        except Exception:
            pass
    return {
        "id": str(r.id),
        "report_id": r.report_id,
        "user_behavior": r.user_behavior if isinstance(r.user_behavior, str) else r.user_behavior.value,
        "can_communicate": r.can_communicate,
        "latitude": lat,
        "longitude": lng,
        "event_define": r.event_define,
        "event_capture": r.event_capture,
        "image_path": r.image_path,
        "report_date": r.report_date.isoformat() if r.report_date else "",
        "created_at": r.created_at,
    }


def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.utcnow()


async def _run_ai_analysis(report_id):
    """BackgroundTask: yeni DB session aç, AI analizi yap, kapat."""
    async with AsyncSessionLocal() as session:
        try:
            await analyze_report(report_id, session)
        except Exception as e:
            logger.error(f"BackgroundTask AI hatası: {e}")


async def _run_ai_analysis_batch(report_ids: list):
    """
    Raporları SIRALI olarak analiz et — rate limit aşımını önler.
    Gemini Free Tier: ~15 RPM → rapor başına ~8sn bekleme (LLM+VLM = 2 istek)
    """
    import asyncio
    global _batch_running

    delay_seconds = float(os.getenv("AI_REPORT_DELAY_SECONDS", "4.5"))
    total = len(report_ids)
    try:
        for idx, rid in enumerate(report_ids, 1):
            async with AsyncSessionLocal() as session:
                try:
                    logger.info(f"🔄 Batch analiz [{idx}/{total}] (delay={delay_seconds}s)")
                    await analyze_report(rid, session)
                    try:
                        await detect_and_assign_cluster(rid, session)
                        logger.info(f"📍 Incremental cluster işlendi [{idx}/{total}]")
                    except Exception as cluster_err:
                        logger.error(f"Batch cluster hatası [{idx}/{total}]: {cluster_err}")
                except Exception as e:
                    logger.error(f"BackgroundTask AI hatası [{idx}/{total}]: {e}")
            if idx < total:
                await asyncio.sleep(delay_seconds)
    finally:
        _batch_running = False


@router.post("/admin/clear-visibility", status_code=status.HTTP_200_OK)
async def admin_clear_visibility(db: AsyncSession = Depends(get_db)):
    """
    Dashboard görünürlüğünü sıfırla.
    processed_reports, disaster_clusters, cluster_reports silinir — raw_reports korunur.
    """
    from sqlalchemy import text
    await db.execute(text("DELETE FROM cluster_reports"))
    await db.execute(text("DELETE FROM disaster_clusters"))
    await db.execute(text("DELETE FROM processed_reports"))
    return {
        "status": "ok",
        "cleared": ["cluster_reports", "disaster_clusters", "processed_reports"],
        "kept": ["raw_reports"],
    }


@router.post("/admin/populate-test-data", status_code=status.HTTP_200_OK)
async def admin_populate_test_data(db: AsyncSession = Depends(get_db)):
    """
    Tüm tabloları sıfırla ve seed/test_reports.json'dan test raporlarını yükle.
    Örnek görseller seed/images/ altından IMAGES_DIR/samples/ altına kopyalanır.
    scripts/populate_reports.py ile eşdeğer — terminal gerektirmez.
    """
    import json as _json
    import shutil
    from sqlalchemy import text

    json_path = settings.SEED_DIR / "test_reports.json"
    if not json_path.exists():
        raise HTTPException(status_code=500, detail=f"test_reports.json bulunamadı ({json_path})")

    with open(json_path, "r", encoding="utf-8") as f:
        data = _json.load(f)

    reports_data = data["raw_reports"]

    seed_images_dir = settings.SEED_DIR / "images"
    samples_dir = settings.IMAGES_DIR / SAMPLE_IMAGES_SUBDIR
    samples_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(
        p.name for p in seed_images_dir.glob("IMG_*") if p.is_file()
    ) if seed_images_dir.exists() else []
    for name in images:
        target = samples_dir / name
        if not target.exists():
            shutil.copy2(seed_images_dir / name, target)

    def _distribute(imgs, n):
        result = [[] for _ in range(n)]
        for i, img in enumerate(imgs):
            result[i % n].append(img)
        return result

    img_dist = _distribute(images, len(reports_data))

    # 1. Tüm tabloları temizle
    await db.execute(text(
        "TRUNCATE cluster_reports, disaster_clusters, processed_reports, exif_file, raw_reports CASCADE"
    ))
    await db.flush()

    # 2. Raporları ekle
    for r in reports_data:
        lon, lat = r["gps_location"]["coordinates"]
        wkt = f"SRID=4326;POINT({lon} {lat})"
        report_obj = Report(
            report_id=r["report_id"],
            user_behavior=r["user_behavior"],
            can_communicate=r["can_communicate"],
            gps_location=wkt,
            event_define=r.get("event_define"),
            event_capture=r.get("event_capture"),
            report_date=_parse_date(r["report_date"]),
            created_at=_parse_date(r["created_at"]).replace(tzinfo=None),
        )
        db.add(report_obj)
    await db.flush()

    # 3. Görselleri eşle
    for i, r in enumerate(reports_data):
        imgs = img_dist[i]
        if not imgs:
            continue
        primary = f"{SAMPLE_IMAGES_SUBDIR}/{imgs[0]}"
        await db.execute(
            text("UPDATE raw_reports SET image_path = :img, event_capture = :img WHERE report_id = :rid"),
            {"img": primary, "rid": r["report_id"]},
        )

    return {
        "status": "ok",
        "reports_inserted": len(reports_data),
        "images_found": len(images),
    }


@router.post("/cluster-all", status_code=status.HTTP_200_OK)
async def cluster_all_reports(
    db: AsyncSession = Depends(get_db),
):
    """
    Tüm cluster yapısını temizler ve raporları DBSCAN ile baştan kümeler.
    Toplu veri yüklemesi sonrası önerilen yöntemdir.
    """
    result = await rebuild_clusters_with_dbscan(db)
    return result


@router.post("/analyze-all", status_code=status.HTTP_200_OK)
async def analyze_all_reports(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    processed_reports'ta kaydı olmayan VEYA severity_score=1 (başarısız LLM)
    olan tüm raporları AI analizine gönder.
    Sıralı çalışır — rate limit aşımını önler.
    """
    global _batch_running

    if _batch_running:
        return {
            "status": "already_running",
            "message": "Batch analiz zaten çalışıyor. Yeni istek kuyruğa alınmadı.",
        }

    # 1. Hiç processed olmamış raporlar
    result = await db.execute(
        select(Report).where(
            ~Report.id.in_(
                select(ProcessedReport.raw_report_id).where(
                    ProcessedReport.raw_report_id.isnot(None)
                )
            )
        )
    )
    unprocessed = result.scalars().all()

    # 2. severity_score=1 olan raporlar (LLM başarısız, yeniden dene)
    failed_result = await db.execute(
        select(Report).where(
            Report.id.in_(
                select(ProcessedReport.raw_report_id).where(
                    ProcessedReport.severity_score <= 1.0,
                    ProcessedReport.raw_report_id.isnot(None),
                )
            )
        )
    )
    failed_reports = failed_result.scalars().all()

    # Birleştir (duplicate olmasın)
    seen_ids = set()
    all_reports = []
    for r in list(unprocessed) + list(failed_reports):
        if r.id not in seen_ids:
            seen_ids.add(r.id)
            all_reports.append(r)

    if not all_reports:
        return {"status": "all_processed", "count": 0}

    # Tüm raporları TEK bir background task içinde SIRALI çalıştır
    report_ids = [r.id for r in all_reports]
    _batch_running = True
    background_tasks.add_task(_run_ai_analysis_batch, report_ids)

    return {
        "status": "queued",
        "count": len(all_reports),
        "unprocessed": len(unprocessed),
        "retry_failed": len(failed_reports),
        "report_ids": [r.report_id for r in all_reports],
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    wkt_point = f"SRID=4326;POINT({payload.longitude} {payload.latitude})"
    new_report = Report(
        report_id=payload.report_id,
        user_behavior=payload.user_behavior,
        can_communicate=payload.can_communicate,
        gps_location=wkt_point,
        event_define=payload.event_define,
        event_capture=payload.event_capture,
        report_date=_parse_date(payload.report_date),
    )
    db.add(new_report)
    await db.flush()
    await db.refresh(new_report)

    # AI analizini arka planda başlat (kullanıcı beklemez)
    background_tasks.add_task(_run_ai_analysis, new_report.id)

    # Cluster tespitini senkron çalıştır (yanıta dahil edebilmek için)
    nearby_cluster = None
    try:
        nearby_cluster = await detect_and_assign_cluster(new_report.id, db)
    except Exception as e:
        logger.error(f"Cluster tespiti hatası: {e}")

    response = _to_response(new_report)
    response["nearby_cluster"] = nearby_cluster
    return response


@router.post("/{report_id}/image", status_code=status.HTTP_200_OK)
async def upload_image(
    report_id: str,
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Mevcut bir report'a görsel ekle.
    Akış: orijinal kaydet → EXIF çıkar → sıkıştır → DB güncelle → AI tetikle
    """
    # Report bul
    result = await db.execute(select(Report).where(Report.report_id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı")

    # 1. Orijinali geçici kaydet (EXIF çıkarma için)
    temp_path = save_original_temp(image.file, image.filename or "upload.jpg")

    # 2. EXIF çıkar (orijinalden — sıkıştırmadan önce)
    exif_data = extract_exif(temp_path)

    # 3. Sıkıştır + kalıcı kaydet
    final_path = compress_and_save(temp_path)
    relative_path = get_relative_path(final_path)

    # 4. raw_reports güncelle
    report.image_path = relative_path
    report.event_capture = relative_path

    # EXIF'ten konum ve tarih varsa raw_reports'a da yaz
    if exif_data.get("latitude") and exif_data.get("longitude"):
        report.image_location = (
            f"SRID=4326;POINT({exif_data['longitude']} {exif_data['latitude']})"
        )
    if exif_data.get("date_taken"):
        report.image_date = exif_data["date_taken"]

    # 5. exif_file tablosuna kaydet
    exif_record = ExifFile(
        report_id=report.id,
        date_taken=exif_data.get("date_taken"),
        latitude=exif_data.get("latitude"),
        longitude=exif_data.get("longitude"),
        altitude=exif_data.get("altitude"),
        gps_direction=exif_data.get("gps_direction"),
        gps_speed=exif_data.get("gps_speed"),
        device_make=exif_data.get("device_make"),
        device_model=exif_data.get("device_model"),
        image_width=exif_data.get("image_width"),
        image_height=exif_data.get("image_height"),
        orientation=exif_data.get("orientation"),
        flash_fired=exif_data.get("flash_fired"),
        iso=exif_data.get("iso"),
        brightness=exif_data.get("brightness"),
        raw_exif=exif_data.get("raw_exif"),
    )
    db.add(exif_record)
    await db.flush()

    # 6. AI analizini tekrar tetikle (artık görseli de analiz edecek)
    background_tasks.add_task(_run_ai_analysis, report.id)

    return {
        "status": "uploaded",
        "image_path": relative_path,
        "exif": {
            "date_taken": str(exif_data.get("date_taken")) if exif_data.get("date_taken") else None,
            "gps": {
                "latitude": exif_data.get("latitude"),
                "longitude": exif_data.get("longitude"),
            },
            "device": f"{exif_data.get('device_make') or ''} {exif_data.get('device_model') or ''}".strip() or None,
            "resolution": f"{exif_data.get('image_width')}x{exif_data.get('image_height')}" if exif_data.get("image_width") else None,
        },
    }


@router.post("/{report_id}/exif", status_code=status.HTTP_200_OK)
async def upload_exif(
    report_id: str,
    payload: ExifUpload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Mobil app'ten ayrı gönderilen EXIF verisini exif_file tablosuna kaydet.
    Görsel içinden EXIF parse edilemediğinde bu endpoint kullanılır.
    Mobil cihaz fotoğraf çekerken EXIF'i ayrıca JSON olarak yakalar ve gönderir.
    """
    # Report bul
    result = await db.execute(select(Report).where(Report.report_id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı")

    # Tarih parse (naive datetime — DB sütunu TIMESTAMP WITHOUT TIME ZONE)
    date_taken = None
    if payload.date_taken:
        try:
            dt = datetime.fromisoformat(payload.date_taken.replace("Z", "+00:00"))
            date_taken = dt.replace(tzinfo=None)  # offset-naive yap
        except (ValueError, TypeError):
            try:
                date_taken = datetime.strptime(payload.date_taken, "%Y:%m:%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

    # Mevcut exif kaydı varsa güncelle, yoksa yeni oluştur
    existing_result = await db.execute(
        select(ExifFile).where(ExifFile.report_id == report.id)
    )
    exif_record = existing_result.scalar_one_or_none()

    if exif_record:
        # Mevcut kaydı güncelle (mobil verisi öncelikli)
        if payload.latitude is not None:
            exif_record.latitude = payload.latitude
        if payload.longitude is not None:
            exif_record.longitude = payload.longitude
        if payload.altitude is not None:
            exif_record.altitude = payload.altitude
        if payload.gps_direction is not None:
            exif_record.gps_direction = payload.gps_direction
        if payload.gps_speed is not None:
            exif_record.gps_speed = payload.gps_speed
        if date_taken:
            exif_record.date_taken = date_taken
        if payload.device_make:
            exif_record.device_make = payload.device_make
        if payload.device_model:
            exif_record.device_model = payload.device_model
        if payload.image_width is not None:
            exif_record.image_width = payload.image_width
        if payload.image_height is not None:
            exif_record.image_height = payload.image_height
        if payload.orientation is not None:
            exif_record.orientation = payload.orientation
        if payload.flash_fired is not None:
            exif_record.flash_fired = payload.flash_fired
        if payload.iso is not None:
            exif_record.iso = payload.iso
        if payload.brightness is not None:
            exif_record.brightness = payload.brightness
        if payload.raw_exif:
            exif_record.raw_exif = {**(exif_record.raw_exif or {}), **payload.raw_exif}
    else:
        # Yeni exif kaydı oluştur
        exif_record = ExifFile(
            report_id=report.id,
            date_taken=date_taken,
            latitude=payload.latitude,
            longitude=payload.longitude,
            altitude=payload.altitude,
            gps_direction=payload.gps_direction,
            gps_speed=payload.gps_speed,
            device_make=payload.device_make,
            device_model=payload.device_model,
            image_width=payload.image_width,
            image_height=payload.image_height,
            orientation=payload.orientation,
            flash_fired=payload.flash_fired,
            iso=payload.iso,
            brightness=payload.brightness,
            raw_exif=payload.raw_exif,
        )
        db.add(exif_record)

    # raw_reports'ta image_location ve image_date güncelle
    if payload.latitude is not None and payload.longitude is not None:
        report.image_location = (
            f"SRID=4326;POINT({payload.longitude} {payload.latitude})"
        )
    if date_taken:
        report.image_date = date_taken

    await db.flush()

    # AI analizini tekrar tetikle (EXIF ile konum doğrulama yapabilir)
    background_tasks.add_task(_run_ai_analysis, report.id)

    logger.info(f"📱 Mobil EXIF kaydedildi: {report_id} — GPS: {payload.latitude},{payload.longitude}")

    return {
        "status": "exif_saved",
        "report_id": report_id,
        "exif": {
            "date_taken": str(date_taken) if date_taken else None,
            "gps": {
                "latitude": payload.latitude,
                "longitude": payload.longitude,
                "altitude": payload.altitude,
            },
            "device": f"{payload.device_make or ''} {payload.device_model or ''}".strip() or None,
            "resolution": f"{payload.image_width}x{payload.image_height}" if payload.image_width else None,
        },
    }


@router.get("/", response_model=ReportListResponse)
async def list_reports(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Report).order_by(Report.created_at.desc())
    )
    reports = result.scalars().all()

    # Tüm processed_reports'u tek query'de çek, raw_report_id ile map'le (N+1 önle)
    proc_result = await db.execute(select(ProcessedReport))
    proc_map = {p.raw_report_id: p for p in proc_result.scalars().all()}

    enriched = []
    for r in reports:
        data = _to_response(r)
        proc = proc_map.get(r.id)
        if proc:
            summary = None
            if proc.llm_analysis:
                try:
                    import json
                    llm = proc.llm_analysis if isinstance(proc.llm_analysis, dict) else json.loads(proc.llm_analysis)
                    summary = llm.get("summary") or llm.get("description")
                except Exception:
                    summary = None
            data["processed_summary"] = summary
            data["processed_user_status"] = proc.user_status
            data["processed_disaster_type"] = proc.disaster_type
            data["severity_score"] = proc.severity_score
            data["processed_location_match_score"] = proc.location_match_score
            # Indicator-based scoring data
            ind = proc.indicators_json or {}
            data["indicators"] = {
                "llm": ind.get("llm_indicators"),
                "vlm": ind.get("vlm_indicators"),
            } if ind else None
            data["category_scores"] = ind.get("category_scores")
            data["active_indicators"] = ind.get("active_indicators")
            data["reliability_score"] = ind.get("reliability_score")
            data["formula_breakdown"] = ind.get("formula_breakdown")
        else:
            data["processed_summary"] = None
            data["processed_user_status"] = None
            data["processed_disaster_type"] = None
            data["severity_score"] = None
            data["processed_location_match_score"] = None
            data["indicators"] = None
            data["category_scores"] = None
            data["active_indicators"] = None
            data["reliability_score"] = None
            data["formula_breakdown"] = None
        enriched.append(data)

    return {"total": len(enriched), "reports": enriched}


@router.get("/{report_id}/detail")
async def get_report_detail(report_id: str, db: AsyncSession = Depends(get_db)):
    """Bir report'un raw + processed + exif bilgilerini döndür (dashboard detay paneli için)."""
    # report_id hem UUID hem RPT-... olabilir
    result = await db.execute(select(Report).where(Report.report_id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        # UUID ile dene
        try:
            result = await db.execute(select(Report).where(Report.id == report_id))
            report = result.scalar_one_or_none()
        except Exception:
            pass
    if not report:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadi")

    # raw data
    raw = _to_response(report)

    # processed_report
    proc_result = await db.execute(
        select(ProcessedReport).where(ProcessedReport.raw_report_id == report.id)
    )
    proc = proc_result.scalar_one_or_none()
    processed = None
    if proc:
        ind = proc.indicators_json or {}
        processed = {
            "disaster_type": proc.disaster_type,
            "severity_score": proc.severity_score,
            "user_status": proc.user_status,
            "llm_analysis": proc.llm_analysis,
            "vlm_analysis": proc.vlm_analysis,
            "indicators_json": proc.indicators_json,
            "location_match_score": proc.location_match_score,
            "processed_at": proc.processed_at.isoformat() if proc.processed_at else None,
            "category_scores": ind.get("category_scores"),
            "active_indicators": ind.get("active_indicators"),
            "cross_validated": ind.get("cross_validated"),
            "reliability_score": ind.get("reliability_score"),
            "formula_breakdown": ind.get("formula_breakdown"),
        }

    # exif
    exif_result = await db.execute(
        select(ExifFile).where(ExifFile.report_id == report.id)
    )
    exif = exif_result.scalar_one_or_none()
    exif_data = None
    if exif:
        exif_data = {
            "device": f"{exif.device_make or ''} {exif.device_model or ''}".strip() or None,
            "date_taken": exif.date_taken.isoformat() if exif.date_taken else None,
            "latitude": exif.latitude,
            "longitude": exif.longitude,
            "resolution": f"{exif.image_width}x{exif.image_height}" if exif.image_width else None,
        }

    return {
        "report": raw,
        "processed": processed,
        "exif": exif_data,
    }


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadi")
    return _to_response(report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(report_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(delete(Report).where(Report.id == report_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadi")
