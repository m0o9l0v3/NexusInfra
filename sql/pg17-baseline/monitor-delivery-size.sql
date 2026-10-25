-- Daily size check of the delivery tables (NexusMobile E2-5 §3.5, Infra #17). Read-only.
-- Run as nexus_public (it has SELECT on both tables) or any role with that SELECT:
--   psql -X -At -F ' ' -d nexus -U nexus_public -f monitor-delivery-size.sql
-- review_required = t means file the deletion-policy review (E2-5 §3.5): the tables together exceed
-- 1 GiB, or participant_bundles holds more than 500 rows. Thresholds are provisional; -v max_bytes=
-- and -v max_rows= override them only for rehearsal.
\set ON_ERROR_STOP on
\if :{?max_bytes}
\else
    \set max_bytes 1073741824
\endif
\if :{?max_rows}
\else
    \set max_rows 500
\endif
BEGIN READ ONLY;
SELECT rows, total_bytes, pg_size_pretty(total_bytes) AS total_pretty,
       (total_bytes > :max_bytes OR rows > :max_rows) AS review_required
  FROM (SELECT (SELECT count(*) FROM delivery.participant_bundles) AS rows,
               pg_total_relation_size('delivery.participant_bundles')
               + pg_total_relation_size('delivery.current_bundle') AS total_bytes) AS sizes;
COMMIT;
