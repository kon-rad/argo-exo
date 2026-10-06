import pytest
from exo_guardian.hashing import approval_hash
from exo_guardian.service import Guardian, InvalidRequest
from exo_guardian.simulate import SimulationError

NOW = 1_800_000_000          # 2027-01-15 08:00 UTC
DAY = 86_400
MODULE = "0x" + "e" * 40
SAFE = "0x" + "b" * 40
REPORT_TX = "0x" + "ab" * 32
ZERO = "0x" + "0" * 64
REQ = {"source": "voice", "intent": {"kind": "send", "summary": "Send 20 USDC to mira.eth"},
       "tx": {"chain_id": 1, "to": "0x" + "A" * 40, "value": "0", "data": "0xA9059CBB"}, "from": SAFE}
QUEUE_KEYS = {"id", "summary", "explanation", "chain", "to", "value", "data", "salt", "verdict_tx", "risk",
              "auto_eligible", "expires_at"}


def true_hash(tx):
    return "0x" + approval_hash(1, MODULE, tx["to"], int(tx["value"]), bytes.fromhex(tx["data"][2:]),
                                bytes.fromhex(tx["salt"][2:])).hex()


class Sim:
    """Stands in for the CRE workflow: answers like runGuard would, and records what it was sent."""

    def __init__(self, verdict="approve", auto=True, report_tx=REPORT_TX, usd_out=20.0, ttl=600, **override):
        self.verdict, self.auto, self.report_tx, self.usd_out, self.ttl, self.override = verdict, auto, report_tx, usd_out, ttl, override
        self.calls = []

    def __call__(self, payload, trigger_index, broadcast):
        self.calls.append((payload, trigger_index, broadcast))
        if trigger_index == 1:
            return {"ok": True, "report_tx": self.report_tx}, 50
        approve = self.verdict == "approve"
        r = {"proposal_id": payload["proposal_id"], "verdict": self.verdict, "risk": "low" if approve else "high",
             "auto_eligible": self.auto, "explanation": "You pay 20 USDC to mira.eth. Nothing else changes." if approve
             else "Refused: from the camera.", "reasons": ["plain transfer"] if approve else ["from the camera"],
             "tx_hash": true_hash(payload["tx"]), "expires_at": payload["requested_at"] + self.ttl if approve else 0,
             "changes": [], "report_tx": self.report_tx, "usd_out": self.usd_out if approve else None}
        r.update(self.override)
        return r, 1234


def guardian(store, sim, clock=None, broadcast=True, salt=b"\x01"):
    return Guardian(store, simulate=sim, clock=clock or (lambda: NOW), salt=lambda n: salt * n,
                    module=MODULE, chain_id=1, broadcast=broadcast)


def test_approve_queues_then_executes(store):
    sim = Sim()
    g = guardian(store, sim)
    r = g.guard(REQ)
    pid = r["proposal_id"]
    assert r["verdict"] == "approve" and r["queued"] is True and r["status"] == "waiting_key"
    assert store.get_status(pid) == "waiting_key"
    payload, trig, bc = sim.calls[0]
    assert trig == 0 and bc is True and payload["proposal_id"] == pid and payload["requested_at"] == NOW
    assert payload["tx"] == {"chain_id": 1, "to": "0x" + "a" * 40, "value": "0", "data": "0xa9059cbb", "salt": "0x" + "01" * 32}
    assert payload["from"] == SAFE and payload["context"] == {"spent_today_usd": 0.0}
    [item] = g.pending()
    assert set(item) == QUEUE_KEYS
    assert item == {"id": pid, "summary": "Send 20 USDC to mira.eth", "explanation": "You pay 20 USDC to mira.eth. Nothing else changes.",
                    "chain": "ethereum", "to": "0x" + "a" * 40, "value": "0", "data": "0xa9059cbb", "salt": "0x" + "01" * 32,
                    "verdict_tx": REPORT_TX, "risk": "low", "auto_eligible": True, "expires_at": NOW + 600}
    sent = "0x" + "5" * 64
    assert g.executed(pid, tx_hash=sent) is True
    assert store.get_status(pid) == "executed" and g.pending() == []
    assert g.executed(pid, tx_hash=sent) is True              # a retried report is fine
    assert g.executed(pid, tx_hash="0x" + "6" * 64) is False   # a different hash is not


def test_large_value_round_trips(store):
    req = dict(REQ, tx=dict(REQ["tx"], value=str(2 ** 255)))
    g = guardian(store, Sim())
    g.guard(req)
    assert g.pending()[0]["value"] == str(2 ** 255)


def test_refused_never_queued(store):
    g = guardian(store, Sim("refuse", False))
    r = g.guard(REQ)
    assert r["queued"] is False and store.get_status(r["proposal_id"]) == "refused" and g.pending() == []


@pytest.mark.parametrize("broadcast,report_tx", [(False, ZERO), (False, REPORT_TX), (False, "")])
def test_dry_run_approval_is_simulated_not_queued(store, broadcast, report_tx):
    g = guardian(store, Sim(report_tx=report_tx), broadcast=broadcast)
    r = g.guard(REQ)
    assert r["verdict"] == "approve" and r["queued"] is False and r["status"] == "simulated"
    assert "simulated, not queued" in r["note"]
    assert store.get_status(r["proposal_id"]) == "simulated" and g.pending() == []


@pytest.mark.parametrize("report_tx", [ZERO, "", None, "0xr", "0x" + "ab" * 31, "ab" * 32])
def test_broadcast_without_a_real_report_tx_is_refused(store, report_tx):
    g = guardian(store, Sim(report_tx=report_tx))
    r = g.guard(REQ)
    assert r["verdict"] == "refuse" and r["queued"] is False and "not written onchain" in r["reasons"][0]
    assert store.get_status(r["proposal_id"]) == "refused" and g.pending() == []


def test_hash_mismatch_is_refused_and_ledgered(store):
    g = guardian(store, Sim(tx_hash="0x" + "c" * 64))
    r = g.guard(REQ)
    assert r["verdict"] == "refuse" and r["reasons"][0] == "approval hash mismatch" and r["queued"] is False
    assert store.get_status(r["proposal_id"]) == "refused" and g.pending() == []
    assert store.recent_calls(1)[0]["reason"] == "approval hash mismatch"


def test_hash_from_another_module_is_refused(store):
    class OtherModule(Sim):
        def __call__(self, payload, trigger_index, broadcast):
            r, ms = super().__call__(payload, trigger_index, broadcast)
            tx = payload["tx"]
            r["tx_hash"] = "0x" + approval_hash(1, "0x" + "f" * 40, tx["to"], 0, bytes.fromhex(tx["data"][2:]),
                                                bytes.fromhex(tx["salt"][2:])).hex()
            return r, ms
    r = guardian(store, OtherModule()).guard(REQ)
    assert r["verdict"] == "refuse" and r["reasons"][0] == "approval hash mismatch"


def test_uppercase_hash_still_matches(store):
    class Upper(Sim):
        def __call__(self, payload, trigger_index, broadcast):
            r, ms = super().__call__(payload, trigger_index, broadcast)
            r["tx_hash"] = "0x" + r["tx_hash"][2:].upper()
            return r, ms
    assert guardian(store, Upper()).guard(REQ)["queued"] is True


def test_result_for_another_proposal_is_refused(store):
    r = guardian(store, Sim(proposal_id="someone-else")).guard(REQ)
    assert r["verdict"] == "refuse" and r["reasons"][0] == "malformed workflow result" and r["queued"] is False


@pytest.mark.parametrize("override", [{"auto_eligible": "yes"}, {"risk": "none"}, {"expires_at": "soon"},
                                      {"explanation": None}, {"reasons": "ok"}, {"verdict": "maybe"}])
def test_malformed_result_is_refused(store, override):
    g = guardian(store, Sim(**override))
    r = g.guard(REQ)
    assert r["verdict"] == "refuse" and r["queued"] is False and g.pending() == []
    assert store.get_status(r["proposal_id"]) == "refused"


def test_already_expired_approval_is_not_queued(store):
    r = guardian(store, Sim(ttl=0)).guard(REQ)
    assert r["queued"] is False and store.get_status(r["proposal_id"]) == "refused"


def test_expired_not_pending(store):
    clock = [NOW]
    g = guardian(store, Sim(), clock=lambda: clock[0])
    g.guard(REQ)
    clock[0] = NOW + 599
    assert len(g.pending()) == 1
    clock[0] = NOW + 600
    assert g.pending() == []


@pytest.mark.parametrize("exc", [SimulationError("no workflow result"), OSError("boom"), KeyError("x")])
def test_simulation_failure_is_recorded_as_refused(store, exc):
    def boom(payload, trigger_index, broadcast):
        raise exc
    r = guardian(store, boom).guard(REQ)
    assert r["verdict"] == "refuse" and r["unavailable"] is True and r["queued"] is False
    assert "Guardian unavailable" in r["explanation"] and "boom" not in r["explanation"]
    assert store.get_status(r["proposal_id"]) == "refused"
    call = store.recent_calls(1)[0]
    assert call["handler"] == "guard" and call["verdict"] == "refuse" and isinstance(call["latency_ms"], int)


def test_requester_cannot_set_salt_context_id_or_clock(store):
    sim = Sim()
    req = dict(REQ, proposal_id="mine", requested_at=1, context={"spent_today_usd": -500},
               tx=dict(REQ["tx"], salt="0x" + "dd" * 32))
    r = guardian(store, sim, salt=b"\x07").guard(req)
    payload = sim.calls[0][0]
    assert r["proposal_id"] != "mine" and payload["proposal_id"] == r["proposal_id"]
    assert payload["tx"]["salt"] == "0x" + "07" * 32 and payload["requested_at"] == NOW
    assert payload["context"] == {"spent_today_usd": 0.0}


def test_spent_today_comes_from_the_ledger(store):
    clock = [NOW]
    sim = Sim(usd_out=20.0)
    g = guardian(store, sim, clock=lambda: clock[0])
    executed = g.guard(REQ)["proposal_id"]
    g.executed(executed, tx_hash="0x" + "5" * 64)
    sim.usd_out = 5.5
    g.guard(REQ)                                   # waiting for the key: counts
    sim.usd_out = 7.0
    failed = g.guard(REQ)["proposal_id"]
    g.executed(failed, error="reverted")           # failed: doesn't count
    Guardian(store, simulate=Sim(usd_out=1000.0), clock=lambda: clock[0], salt=lambda n: b"\x02" * n,
             module=MODULE, chain_id=1, broadcast=False).guard(REQ)   # dry run: doesn't count
    guardian(store, Sim("refuse")).guard(REQ)                          # refused: doesn't count
    sim.calls.clear()
    g.guard(REQ)
    assert sim.calls[0][0]["context"] == {"spent_today_usd": 25.5}

    clock[0] = NOW + 601                           # both waiting ones expired: only the executed 20 counts
    sim.calls.clear()
    g.guard(REQ)
    assert sim.calls[0][0]["context"] == {"spent_today_usd": 20.0}

    clock[0] = (NOW // DAY + 1) * DAY              # next UTC day
    sim.calls.clear()
    g.guard(REQ)
    assert sim.calls[0][0]["context"]["spent_today_usd"] == 0.0


def test_executed_with_error_marks_failed(store):
    g = guardian(store, Sim())
    pid = g.guard(REQ)["proposal_id"]
    assert g.executed(pid, error="execution reverted") is True
    assert store.get_status(pid) == "failed" and g.pending() == []
    assert g.executed(pid, tx_hash="0x" + "5" * 64) is False   # no executing a failed one afterwards


def test_executed_only_from_waiting_key(store):
    g = guardian(store, Sim("refuse"))
    pid = g.guard(REQ)["proposal_id"]
    assert g.executed(pid, tx_hash="0x" + "5" * 64) is False and store.get_status(pid) == "refused"
    assert g.executed("00000000-0000-0000-0000-000000000009", tx_hash="0x" + "5" * 64) is False


@pytest.mark.parametrize("pid,kw", [("not-a-uuid", {"tx_hash": "0x" + "5" * 64}),
                                    ("00000000-0000-0000-0000-000000000009", {"tx_hash": "0x5"}),
                                    ("00000000-0000-0000-0000-000000000009", {}),
                                    ("00000000-0000-0000-0000-000000000009", {"tx_hash": "0x" + "5" * 64, "error": "x"})])
def test_executed_rejects_bad_input(store, pid, kw):
    with pytest.raises(InvalidRequest):
        guardian(store, Sim()).executed(pid, **kw)


BAD = [{}, {"tx": "x", "from": SAFE, "intent": REQ["intent"]},
       dict(REQ, tx=dict(REQ["tx"], to="0x123")), dict(REQ, tx=dict(REQ["tx"], value="-1")),
       dict(REQ, tx=dict(REQ["tx"], value="1.5")), dict(REQ, tx=dict(REQ["tx"], value=True)),
       dict(REQ, tx=dict(REQ["tx"], data="0xabc")), dict(REQ, tx=dict(REQ["tx"], data="nothex")),
       dict(REQ, tx=dict(REQ["tx"], chain_id="1")), dict(REQ, tx=dict(REQ["tx"], value=str(2 ** 256))),
       dict(REQ, **{"from": "me"}), dict(REQ, intent={"summary": "no kind"}), dict(REQ, intent="send"),
       dict(REQ, source=7)]


@pytest.mark.parametrize("req", BAD)
def test_invalid_request_raises_before_any_ledger_row(store, req):
    sim = Sim()
    with pytest.raises(InvalidRequest):
        guardian(store, sim).guard(req)
    assert sim.calls == [] and store.recent_calls(5) == []


def test_intent_is_trimmed_to_the_schema(store):
    sim = Sim()
    guardian(store, sim).guard(dict(REQ, intent={"kind": "send", "summary": "x", "amount": "20", "evil": {"a": 1}}))
    assert sim.calls[0][0]["intent"] == {"kind": "send", "summary": "x", "amount": "20"}


def test_freeze(store):
    sim = Sim()
    r = guardian(store, sim).freeze("lost the deck")
    assert r == {"ok": True, "report_tx": REPORT_TX, "simulated": False}
    assert sim.calls[0] == ({"reason": "lost the deck"}, 1, True)
    call = store.recent_calls(1)[0]
    assert call["handler"] == "freeze" and call["verdict"] == "frozen"


@pytest.mark.parametrize("broadcast,report_tx", [(False, ZERO), (True, ZERO), (True, "")])
def test_freeze_that_did_not_land_is_not_ok(store, broadcast, report_tx):
    r = guardian(store, Sim(report_tx=report_tx), broadcast=broadcast).freeze("panic")
    assert r["ok"] is False and r["simulated"] is (not broadcast)


def test_freeze_failure_is_unavailable(store):
    def boom(payload, trigger_index, broadcast):
        raise SimulationError("x")
    r = guardian(store, boom).freeze("panic")
    assert r == {"ok": False, "report_tx": "", "simulated": False, "unavailable": True}
    assert store.recent_calls(1)[0]["verdict"] == "error"


def test_freeze_reason_is_cleaned():
    from exo_guardian.store import MemoryStore
    sim = Sim()
    guardian(MemoryStore(), sim).freeze("a\nb" + "x" * 500)
    assert sim.calls[0][0]["reason"] == "a b" + "x" * 197


def test_cli(capsys):
    import json
    from exo_guardian.cli import main
    from exo_guardian.store import MemoryStore
    mk = lambda sim: (lambda: guardian(MemoryStore(), sim))  # noqa: E731
    assert main(["guard", json.dumps(REQ)], make_guardian=mk(Sim())) == 0
    assert json.loads(capsys.readouterr().out)["queued"] is True

    def boom(payload, trigger_index, broadcast):
        raise SimulationError("x")
    assert main(["guard", json.dumps(REQ)], make_guardian=mk(boom)) == 1
    assert json.loads(capsys.readouterr().out)["unavailable"] is True
    assert main(["guard", "{not json"], make_guardian=mk(Sim())) == 2
    assert main(["guard", "{}"], make_guardian=mk(Sim())) == 2
    assert main(["freeze"], make_guardian=mk(Sim())) == 2
    assert main(["guard", "{}"], env={}) == 2   # no DSN


def test_binding_comes_from_the_workflow_config():
    from exo_guardian.service import workflow_binding
    chain_id, module = workflow_binding()
    assert chain_id == 1 and module.startswith("0x") and len(module) == 42


def test_default_is_a_dry_run(store):
    g = Guardian(store, simulate=Sim(), clock=lambda: NOW, module=MODULE, chain_id=1)
    assert g.broadcast is False and g.guard(REQ)["status"] == "simulated" and g.pending() == []
