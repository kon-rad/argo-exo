import json

import pytest

from fund_trade import cli, rules, store

VOTES = [
    {"partner": "Wren Hollis", "vote": "yes", "reason": "liquidity rising"},
    {"partner": "Kaito Brandt", "vote": "yes", "reason": "fees up"},
    {"partner": "Odalys Fen", "vote": "yes", "reason": "small size"},
    {"partner": "Juno Achebe", "vote": "abstain", "reason": "no catalyst"},
    {"partner": "Basile Marr", "vote": "no", "reason": "crowded"},
]


def make(tmp_path, votes=VOTES, now=1_760_400_000.0):
    return store.create(tmp_path, meeting="2026-10-14", motion="Start the ETH core position",
                        intent={"sell": "USDC", "buy": "WETH", "amount": "30"}, votes=votes, now=now)


def test_create_numbers_per_day_and_tallies(tmp_path):
    a, b = make(tmp_path), make(tmp_path)
    assert a["id"] == "2025-10-14-1" and b["id"] == "2025-10-14-2"
    assert a["tally"] == "3–1 (Basile against)" and a["status"] == "proposed"


def test_create_needs_three_yes_and_no_block(tmp_path):
    two = [dict(v, vote="no") if i < 3 else v for i, v in enumerate(VOTES)]
    with pytest.raises(store.StoreError):
        make(tmp_path, two)
    blocked = [dict(v, vote="block") if v["partner"].startswith("Odalys") else v for v in VOTES]
    with pytest.raises(store.StoreError):
        make(tmp_path, blocked)
    with pytest.raises(store.StoreError):
        make(tmp_path, [{"partner": "X", "vote": "maybe"}])


def test_bad_ids_never_become_paths(tmp_path):
    for bad in ("../x", "2026-10-14-1/../../etc", "", "abc"):
        with pytest.raises(store.StoreError):
            store.path_for(tmp_path, bad)


def test_expire_after_48h(tmp_path):
    p = make(tmp_path, now=1000.0)
    assert store.expire(tmp_path, now=1000.0 + 47 * 3600) == []
    assert store.expire(tmp_path, now=1000.0 + 49 * 3600) == [p["id"]]
    assert store.load(tmp_path, p["id"])["status"] == "expired"


def test_trade_log_newest_first_and_nav(tmp_path):
    log = tmp_path / "trade-log.md"
    log.write_text(f"# Trade Log\n\n**NAV:** $100.00 {store.NAV_MARK}\n\n{store.ENTRIES_MARK}\n")
    store.log_entry(log, "### one")
    store.log_entry(log, "### two")
    text = log.read_text()
    assert text.index("### two") < text.index("### one")
    store.set_nav(log, "**NAV:** $101.50")
    assert f"**NAV:** $101.50 {store.NAV_MARK}" in log.read_text()


def test_minimal_rules():
    i = {"sell": "USDC", "buy": "WETH", "amount": "30"}
    assert rules.check(i, {}, {"slippage_bps": 100, "executed_today": 1}) == []
    assert rules.check(i, {}) == []
    assert any("slippage" in v for v in rules.check(i, {}, {"slippage_bps": 150}))
    assert any("today" in v for v in rules.check(i, {}, {"executed_today": 2}))
    assert "trades/day" in rules.describe()


def test_notify_without_token_is_a_noop():
    from fund_trade import notify
    assert notify.send("hi", env={"FUND_ENV_FILE": "/nonexistent"}) is False


def test_cli_propose_list_show_refuse_log(tmp_path):
    env = {"FUND_DIR": str(tmp_path), "FUND_AGENT_KEY": str(tmp_path / "k")}
    (tmp_path / "constitution.md").write_text("# C\n\n## 5. Current thesis\nHold the core.\n\n## 6. Next\nx\n")
    out = cli.run(["propose", "--meeting", "2026-10-14", "--motion", "Buy ETH", "--sell", "USDC", "--buy", "WETH",
                   "--amount", "30", "--votes", json.dumps(VOTES)], env)
    pid = out["id"]
    assert [p["id"] for p in cli.run(["proposals"], env)["proposals"]] == [pid]
    assert cli.run(["show", pid], env)["votes"][0]["partner"] == "Wren Hollis"
    assert cli.run(["refuse", pid], env)["status"] == "refused"
    assert cli.run(["proposals"], env)["proposals"] == []
    lg = cli.run(["log"], env)
    assert lg["thesis"] == "Hold the core."
    assert "Refused: sell 30 USDC for WETH" in lg["entries"][0]


def test_cli_errors_are_json(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FUND_DIR", str(tmp_path))
    assert cli.main(["show", "2026-01-01-1"]) == 1
    assert "no proposal" in json.loads(capsys.readouterr().out)["error"]


def test_agent_key_is_created_600_and_never_in_config(tmp_path):
    from fund_trade.config import Fund
    f = Fund.from_env({"FUND_DIR": str(tmp_path), "FUND_AGENT_KEY": str(tmp_path / "keys" / "agent.key")})
    addr = f.init_agent_key()
    assert (tmp_path / "keys" / "agent.key").stat().st_mode & 0o077 == 0
    assert f.init_agent_key() == addr            # idempotent
    f.save_cfg()
    assert f.agent_key() not in (tmp_path / "state" / "config.json").read_text()


def test_owners_must_all_be_set(tmp_path):
    from fund_trade.config import ConfigError, Fund
    f = Fund.from_env({"FUND_DIR": str(tmp_path), "FUND_AGENT_KEY": str(tmp_path / "k"),
                       "FUND_DECK_OWNER": "0x" + "11" * 20})
    f.init_agent_key()
    with pytest.raises(ConfigError, match="phone"):
        f.owners()


def test_withdraw_needs_a_savings_address_and_logs_refusals(tmp_path):
    env = {"FUND_DIR": str(tmp_path), "FUND_AGENT_KEY": str(tmp_path / "k")}
    with pytest.raises(Exception, match="savings"):
        cli.run(["withdraw", "--token", "USDC", "--amount", "10"], env)
    env["FUND_SAVINGS"] = "0x" + "5a" * 20
    out = cli.run(["withdraw", "--token", "USDC", "--amount", "all", "--reason", "profits"], env)
    p = cli.run(["show", out["id"]], env)
    assert p["kind"] == "withdraw" and p["tally"] == "requested by Konrad" and out["to"] == env["FUND_SAVINGS"]
    cli.run(["refuse", out["id"]], env)
    assert "Refused: move all USDC to savings" in cli.run(["log"], env)["entries"][0]
