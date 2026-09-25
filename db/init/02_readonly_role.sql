-- Read-only role used by the API. The translator only ever emits SELECT,
-- and the database account cannot write or change structure either.
CREATE ROLE app_ro WITH LOGIN PASSWORD 'app_ro_secret';
GRANT CONNECT ON DATABASE appdb TO app_ro;
GRANT USAGE ON SCHEMA public TO app_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO app_ro;
