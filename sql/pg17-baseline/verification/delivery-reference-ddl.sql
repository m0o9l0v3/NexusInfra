-- VERIFICATION ONLY. Stand-in for the Studio migration that creates the delivery tables
-- (NexusStudio#11). Transcribes NexusMobile E2-5 §3.1 and the monotonic trigger of §3.2 so the
-- contract B revision can be rehearsed before Studio ships its migration. Never apply this to
-- production: there the reviewed Studio migration SQL, run by the Studio migrator, is the source.
-- Run as the Studio migrator (owner of schema delivery).
\set ON_ERROR_STOP on
BEGIN;
CREATE TABLE delivery.participant_bundles (
    bundle_version  bigint      PRIMARY KEY,
    release_id      uuid        NOT NULL UNIQUE,
    schema_version  text        NOT NULL,
    payload         bytea       NOT NULL,
    sha256          char(64)    NOT NULL,
    byte_length     integer     NOT NULL,
    created_at      timestamptz NOT NULL
);
CREATE TABLE delivery.current_bundle (
    singleton       boolean     PRIMARY KEY DEFAULT true CHECK (singleton),
    bundle_version  bigint      NOT NULL REFERENCES delivery.participant_bundles(bundle_version),
    updated_at      timestamptz NOT NULL
);
CREATE FUNCTION delivery.reject_bundle_version_decrease() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
    IF NEW.bundle_version < OLD.bundle_version THEN
        RAISE EXCEPTION 'delivery.current_bundle.bundle_version must not decrease (% -> %)',
            OLD.bundle_version, NEW.bundle_version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;
CREATE TRIGGER current_bundle_monotonic BEFORE UPDATE ON delivery.current_bundle
    FOR EACH ROW EXECUTE FUNCTION delivery.reject_bundle_version_decrease();
COMMIT;
