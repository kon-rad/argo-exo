-- Run as the database owner after schema.sql and views.sql, with fresh passwords that go only into
-- bridge.env / guardian.env:
--   psql -d exo -v writer_pw="$(openssl rand -hex 24)" -v reader_pw="$(openssl rand -hex 24)" -f roles.sql
-- Re-runnable: an existing role is left as it is (its password is not changed).
SELECT format('CREATE ROLE exo_writer LOGIN PASSWORD %L', :'writer_pw')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'exo_writer') \gexec
SELECT format('CREATE ROLE exo_reader LOGIN PASSWORD %L', :'reader_pw')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'exo_reader') \gexec

-- guardian-run: no DELETE, no DDL.
GRANT SELECT, INSERT, UPDATE ON proposals, verdicts, executions, cre_calls TO exo_writer;
GRANT USAGE ON SEQUENCE cre_calls_id_seq TO exo_writer;

-- The kiosk: the two views only. The views run with their owner's rights, so no grant on the base tables.
REVOKE ALL ON proposals, verdicts, executions, cre_calls FROM exo_reader;
GRANT SELECT ON exo_tx_v, exo_cre_calls_v TO exo_reader;
