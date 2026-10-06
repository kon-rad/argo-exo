-- The Guardian ledger (03-guardian Task 6). Written by guardian-run as exo_writer; read by the kiosk only through
-- views.sql as exo_reader. Idempotent: safe to re-run.
--
-- status: proposed     row written, simulation not finished (or the runner died mid-run)
--         refused      the workflow refused, the simulator failed, or the runner rejected the workflow's result
--         simulated    the workflow approved in a dry run (no --broadcast): nothing onchain, never queued
--         waiting_key  approved onchain (real report tx) and in the deck's approval queue until it expires
--         executed     the deck's key executed it (executions.tx_hash)
--         failed       the deck's key tried and failed (executions.error)
CREATE TABLE IF NOT EXISTS proposals (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,
  intent jsonb NOT NULL,
  chain text NOT NULL DEFAULT 'ethereum',
  to_addr text NOT NULL,
  value_wei numeric NOT NULL CHECK (value_wei >= 0),
  data text NOT NULL,
  salt text NOT NULL,
  status text NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed', 'refused', 'simulated', 'waiting_key', 'executed', 'failed')));

CREATE TABLE IF NOT EXISTS verdicts (
  proposal_id uuid PRIMARY KEY REFERENCES proposals(id),
  verdict text NOT NULL CHECK (verdict IN ('approve', 'refuse')),
  risk text NOT NULL,
  auto_eligible boolean NOT NULL,
  explanation text NOT NULL,
  reasons jsonb NOT NULL,
  tx_hash text NOT NULL,          -- the approval hash ('' when the workflow never got that far)
  expires_at timestamptz NOT NULL, -- epoch 0 when there is no approval
  usd_out numeric,                 -- USD leaving the Safe, as the workflow priced it (approvals only)
  report_tx text);                 -- the onchain report tx ('' or the zero hash in a dry run)

CREATE TABLE IF NOT EXISTS executions (
  proposal_id uuid PRIMARY KEY REFERENCES proposals(id),
  tx_hash text,
  error text,
  executed_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS cre_calls (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  handler text NOT NULL,
  trigger text NOT NULL,
  verdict text,
  reason text,
  latency_ms integer,
  proposal_id uuid REFERENCES proposals(id));

CREATE INDEX IF NOT EXISTS proposals_status_created ON proposals (status, created_at);
