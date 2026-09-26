-- OASIS veritabanı başlangıç scripti
-- PostGIS uzantısını etkinleştir (coğrafi sorgular için)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Zaman dilimini UTC olarak ayarla
SET timezone = 'UTC';

-- User behavior enum type
DO $$ BEGIN
    CREATE TYPE user_behavior_type AS ENUM ('victim', 'observer');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── raw_reports ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       VARCHAR(30) NOT NULL UNIQUE,
    user_behavior   user_behavior_type NOT NULL,
    can_communicate BOOLEAN NOT NULL DEFAULT TRUE,
    gps_location    GEOMETRY(Point, 4326),
    event_define    TEXT,
    event_capture   TEXT,
    image_path      TEXT,
    image_location  GEOMETRY(Point, 4326),
    image_date      TIMESTAMP WITH TIME ZONE,
    report_date     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_reports_gps ON raw_reports USING GIST(gps_location);
CREATE INDEX IF NOT EXISTS idx_raw_reports_date ON raw_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_raw_reports_report_id ON raw_reports(report_id);

-- ── processed_reports ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS processed_reports (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id            VARCHAR(30) NOT NULL UNIQUE,
    raw_report_id        UUID REFERENCES raw_reports(id) ON DELETE CASCADE,
    user_behavior        user_behavior_type NOT NULL,
    can_communicate      BOOLEAN NOT NULL,
    user_status          TEXT,
    gps_location         GEOMETRY(Point, 4326),
    disaster_type        TEXT,
    severity_score       DOUBLE PRECISION CHECK (severity_score >= 1 AND severity_score <= 10),
    vlm_analysis         JSONB,
    llm_analysis         JSONB,
    indicators_json      JSONB,
    location_match_score DOUBLE PRECISION CHECK (location_match_score >= 0 AND location_match_score <= 1),
    user_location        GEOMETRY(Point, 4326),
    image_date           TIMESTAMP WITH TIME ZONE,
    report_date          TIMESTAMP WITH TIME ZONE,
    processed_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processed_reports_report_id ON processed_reports(report_id);
CREATE INDEX IF NOT EXISTS idx_processed_reports_severity ON processed_reports(severity_score DESC);
CREATE INDEX IF NOT EXISTS idx_processed_reports_location ON processed_reports USING GIST(user_location);

-- ── exif_file ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exif_file (
    id            SERIAL PRIMARY KEY,
    report_id     UUID REFERENCES raw_reports(id) ON DELETE CASCADE,
    date_taken    TIMESTAMP,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    altitude      DOUBLE PRECISION,
    gps_direction DOUBLE PRECISION,
    gps_speed     DOUBLE PRECISION,
    device_make   VARCHAR(100),
    device_model  VARCHAR(100),
    image_width   INTEGER,
    image_height  INTEGER,
    orientation   SMALLINT,
    flash_fired   BOOLEAN,
    iso           INTEGER,
    brightness    DOUBLE PRECISION,
    raw_exif      JSONB,
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exif_file_report ON exif_file(report_id);
CREATE INDEX IF NOT EXISTS idx_exif_file_gps ON exif_file(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_exif_file_date ON exif_file(date_taken);

-- ── disaster_clusters ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS disaster_clusters (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_center   GEOMETRY(Point, 4326),
    initial_center   GEOMETRY(Point, 4326),
    radius_meters    DOUBLE PRECISION DEFAULT 100.0,
    event_type       TEXT,
    report_count     INTEGER NOT NULL DEFAULT 1,
    severity_avg     DOUBLE PRECISION,
    first_report_at  TIMESTAMP WITH TIME ZONE,
    last_report_at   TIMESTAMP WITH TIME ZONE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    is_locked        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clusters_center ON disaster_clusters USING GIST(cluster_center);
CREATE INDEX IF NOT EXISTS idx_clusters_active ON disaster_clusters(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_clusters_event ON disaster_clusters(event_type);

-- ── cluster_reports (junction) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cluster_reports (
    cluster_id  UUID NOT NULL REFERENCES disaster_clusters(id) ON DELETE CASCADE,
    report_id   UUID NOT NULL REFERENCES raw_reports(id) ON DELETE CASCADE,
    joined_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cluster_id, report_id)
);

CREATE INDEX IF NOT EXISTS idx_cluster_reports_cluster ON cluster_reports(cluster_id);
CREATE INDEX IF NOT EXISTS idx_cluster_reports_report ON cluster_reports(report_id);

-- ── Migration: eski şemayla oluşturulmuş veritabanlarını güncelle ─────────────
ALTER TABLE processed_reports ADD COLUMN IF NOT EXISTS indicators_json JSONB;
ALTER TABLE disaster_clusters ADD COLUMN IF NOT EXISTS initial_center GEOMETRY(Point, 4326);
ALTER TABLE disaster_clusters ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE;
