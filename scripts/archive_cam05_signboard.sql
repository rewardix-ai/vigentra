-- 9 Oct 2026: move the cam05 signboard reads ("75750 83008" read as TS57SDB3008, TS75QB3008 ... ; 58 rows)
-- out of plate_sightings and camera_plates.cam05 into archive tables. Reversible: insert them back.
-- New reads are already dropped at the edge (services/edge-worker/config/scene_text.yaml).
--   docker exec -i vigentra-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < scripts/archive_cam05_signboard.sql
begin;
create table if not exists plate_sightings_archive_20261009 (like plate_sightings including defaults);
alter table plate_sightings_archive_20261009 add column if not exists archived_reason text;
with moved as (
  delete from plate_sightings s using cameras c
  where c.camera_id = s.camera_id and c.external_camera_id = 'GRID-cam05'
    and s.plate_normalised like 'TS%' and s.plate_normalised ~ '(3008|8300)'
  returning s.*)
insert into plate_sightings_archive_20261009
select moved.*, 'cam05 signboard 75750 83008 misread as a plate (by-eye review 9 Oct)' from moved;
create table if not exists camera_plates.cam05_archive_20261009 (like camera_plates.cam05 including defaults);
with moved as (delete from camera_plates.cam05 where plate like 'TS%' and plate ~ '(3008|8300)' returning *)
insert into camera_plates.cam05_archive_20261009 select * from moved;
select (select count(*) from plate_sightings_archive_20261009) as archived_sightings,
       (select count(*) from camera_plates.cam05_archive_20261009) as archived_cam05_rows;
commit;
