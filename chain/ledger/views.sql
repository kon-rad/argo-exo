-- The contract read by 05-kiosk through exo-bridge (00-architecture §4.6). exo_reader may SELECT these and nothing else.
CREATE OR REPLACE VIEW exo_tx_v AS
  SELECT p.id::text AS id, p.created_at, p.chain, p.intent->>'summary' AS summary, p.to_addr,
         (p.value_wei / 1e18)::text || ' ETH' AS value_text, p.status, v.verdict, v.reasons->>0 AS reason,
         e.tx_hash, p.source
  FROM proposals p LEFT JOIN verdicts v ON v.proposal_id = p.id LEFT JOIN executions e ON e.proposal_id = p.id;

CREATE OR REPLACE VIEW exo_cre_calls_v AS
  SELECT id, created_at, handler, trigger, verdict, reason, latency_ms, proposal_id::text AS tx_id FROM cre_calls;
