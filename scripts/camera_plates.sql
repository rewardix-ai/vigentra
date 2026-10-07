-- One table per grid camera (the original 30, cam01-cam30) holding every plate reading from that
-- camera, kept in step with
-- public.plate_sightings by a trigger. The platform keeps working from plate_sightings (watchlist
-- matching, search and the cross-camera trace need every camera in one table); these tables are a
-- per-camera view of the same rows for reviewing and exporting what each camera read.
--
-- Safe to run again: everything is IF NOT EXISTS / OR REPLACE, and the backfill skips rows that are
-- already copied. A failure to copy is logged as a warning and never blocks the ingest itself.
--
--   docker exec -i vigentra-postgres-1 psql -U sentinel -d sentinel < scripts/camera_plates.sql
--
-- Table names: the grid camera's number (GRID-cam06 -> camera_plates.cam06); each table's comment
-- names its camera. Readings from other cameras (the Delhi test clip, the London feeds, the mock
-- departments) stay in plate_sightings only.

CREATE SCHEMA IF NOT EXISTS camera_plates;

CREATE TABLE IF NOT EXISTS camera_plates._template (
    sighting_id  text PRIMARY KEY,
    plate        text NOT NULL,          -- normalised reading, what the watchlist is matched against
    plate_raw    text,                   -- what the reader produced
    state_code   text,
    read_at      timestamptz NOT NULL,
    read_at_ist  timestamp,
    confidence   real,
    frames       integer,                -- frames that agreed on the reading
    confirmed    boolean,                -- false: an unconfirmed (review) reading
    reader       text,
    is_demo      boolean,
    track_id     integer,
    camera_id    text NOT NULL,
    camera_name  text,
    stored_at    timestamptz NOT NULL DEFAULT now()
);

-- Time in the video. Each grid camera plays a recording on its own date and clock, restarted by the
-- server from time to time. scripts/osd/clock.py samples each camera's on-screen clock into
-- video_clock (offset = video time - our IST time); collect_plates.py records each restart it sees
-- (the end of a refusal window) in grid_restarts. A reading's video time uses the nearest sample of
-- its camera taken in the same server run; NULL when that run has no sample (yet).
CREATE TABLE IF NOT EXISTS camera_plates.video_clock (
    id             bigserial PRIMARY KEY,
    camera         text NOT NULL,          -- cam01..cam30
    wall_ist       timestamp NOT NULL,     -- our clock when the frame was read
    video_time     timestamp NOT NULL,     -- the camera's on-screen clock in that frame
    offset_seconds integer NOT NULL,
    sampled_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS video_clock_cam_time ON camera_plates.video_clock (camera, wall_ist);
CREATE TABLE IF NOT EXISTS camera_plates.grid_restarts (resumed_ist timestamp PRIMARY KEY);

ALTER TABLE camera_plates._template ADD COLUMN IF NOT EXISTS read_at_video timestamp;
DO $$ DECLARE r record; BEGIN
    FOR r IN SELECT table_name FROM information_schema.tables WHERE table_schema = 'camera_plates' AND table_name ~ '^cam[0-9]+$' LOOP
        EXECUTE format('ALTER TABLE camera_plates.%I ADD COLUMN IF NOT EXISTS read_at_video timestamp', r.table_name);
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION camera_plates.video_time_for(cam text, at timestamp) RETURNS timestamp
LANGUAGE sql STABLE AS $$
    WITH run AS (
        SELECT (SELECT max(resumed_ist) FROM camera_plates.grid_restarts WHERE resumed_ist <= at) AS run_start,
               (SELECT min(resumed_ist) FROM camera_plates.grid_restarts WHERE resumed_ist > at) AS run_end)
    SELECT at + make_interval(secs => v.offset_seconds)
    FROM camera_plates.video_clock v, run
    WHERE v.camera = cam
      AND (run.run_start IS NULL OR v.wall_ist >= run.run_start)
      AND (run.run_end IS NULL OR v.wall_ist < run.run_end)
    ORDER BY abs(extract(epoch FROM v.wall_ist - at))
    LIMIT 1
$$;

-- fill read_at_video wherever a sample now covers a reading; clock.py calls this after sampling
CREATE OR REPLACE FUNCTION camera_plates.refresh_video_times() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE r record; n integer := 0; k integer;
BEGIN
    FOR r IN SELECT table_name FROM information_schema.tables WHERE table_schema = 'camera_plates' AND table_name ~ '^cam[0-9]+$' LOOP
        EXECUTE format('UPDATE camera_plates.%I SET read_at_video = camera_plates.video_time_for(%L, read_at_ist) WHERE read_at_video IS NULL', r.table_name, r.table_name);
        GET DIAGNOSTICS k = ROW_COUNT; n := n + k;
    END LOOP;
    RETURN n;
END $$;

-- cam01..cam30 for a grid camera, NULL for any other camera
CREATE OR REPLACE FUNCTION camera_plates.table_for(cam text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT lower(substring(external_camera_id FROM '^GRID-(cam[0-9]+)$')) FROM public.cameras WHERE camera_id = cam LIMIT 1
$$;

CREATE OR REPLACE FUNCTION camera_plates.ensure_table(cam text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    t text := camera_plates.table_for(cam);
    label text;
BEGIN
    IF t IS NULL THEN RETURN NULL; END IF;
    EXECUTE format('CREATE TABLE IF NOT EXISTS camera_plates.%I (LIKE camera_plates._template INCLUDING ALL)', t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON camera_plates.%I (plate)', t || '_plate_idx', t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON camera_plates.%I (read_at)', t || '_time_idx', t);
    SELECT name || ' (' || camera_id || ')' INTO label FROM public.cameras WHERE camera_id = cam LIMIT 1;
    EXECUTE format('COMMENT ON TABLE camera_plates.%I IS %L', t, coalesce(label, cam));
    RETURN t;
END $$;

CREATE OR REPLACE FUNCTION camera_plates.store(s public.plate_sightings) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    t text := camera_plates.table_for(s.camera_id);
    sql text;
BEGIN
    IF t IS NULL THEN RETURN; END IF;
    sql := format('INSERT INTO camera_plates.%I (sighting_id, plate, plate_raw, state_code, read_at, read_at_ist,
                       confidence, frames, confirmed, reader, is_demo, track_id, camera_id, camera_name, read_at_video)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, camera_plates.video_time_for(%L, $6))
                   ON CONFLICT (sighting_id) DO NOTHING', t, t);
    BEGIN
        EXECUTE sql USING s.sighting_id, s.plate_normalised, s.plate_text, s.state_code, s.timestamp_utc,
            s.timestamp_utc AT TIME ZONE 'Asia/Kolkata', s.confidence, s.observations,
            (s.provenance ->> 'plate_confirmed')::boolean, s.reader, s.is_demo_data,
            (s.provenance ->> 'track_id')::integer, s.camera_id,
            (SELECT name FROM public.cameras WHERE camera_id = s.camera_id LIMIT 1);
    EXCEPTION WHEN undefined_table THEN
        PERFORM camera_plates.ensure_table(s.camera_id);
        EXECUTE sql USING s.sighting_id, s.plate_normalised, s.plate_text, s.state_code, s.timestamp_utc,
            s.timestamp_utc AT TIME ZONE 'Asia/Kolkata', s.confidence, s.observations,
            (s.provenance ->> 'plate_confirmed')::boolean, s.reader, s.is_demo_data,
            (s.provenance ->> 'track_id')::integer, s.camera_id,
            (SELECT name FROM public.cameras WHERE camera_id = s.camera_id LIMIT 1);
    END;
END $$;

CREATE OR REPLACE FUNCTION camera_plates.copy_sighting() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        PERFORM camera_plates.store(NEW);
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'camera_plates: could not copy sighting %: %', NEW.sighting_id, SQLERRM;
    END;
    RETURN NULL;
END $$;

-- a table for each grid camera, so each one exists before its first reading; no table for any other
DO $$ DECLARE r record; BEGIN
    FOR r IN SELECT table_name FROM information_schema.tables
             WHERE table_schema = 'camera_plates' AND table_name !~ '^(cam[0-9]+|_template|video_clock|grid_restarts)$' LOOP
        EXECUTE format('DROP TABLE camera_plates.%I', r.table_name);
    END LOOP;
END $$;
SELECT count(camera_plates.ensure_table(camera_id)) AS camera_tables FROM public.cameras WHERE external_camera_id LIKE 'GRID-%';

DROP TRIGGER IF EXISTS camera_plates_copy ON public.plate_sightings;
CREATE TRIGGER camera_plates_copy AFTER INSERT ON public.plate_sightings
    FOR EACH ROW EXECUTE FUNCTION camera_plates.copy_sighting();

-- grid readings stored before the trigger existed
SELECT count(camera_plates.store(s)) AS backfilled FROM public.plate_sightings s
JOIN public.cameras c ON c.camera_id = s.camera_id WHERE c.external_camera_id LIKE 'GRID-%';

