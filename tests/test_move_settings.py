"""config/move.yaml loading, validation and the loop guard."""
from __future__ import annotations

import pytest
import yaml
from test_move_support import at, make_rules, make_state

from prism.engine.config import ConfigError
from prism.move import stage as move_stage
from prism.move.settings import DEFAULT_MOVE_PATH, get_config, load_move_config, use_config


def mutated(tmp_path, fn):
    raw = yaml.safe_load(DEFAULT_MOVE_PATH.read_text(encoding="utf-8"))
    fn(raw)
    p = tmp_path / "move.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return p


def test_shipped_config_loads_with_the_documented_values():
    c = load_move_config()
    assert c.quiet_minutes == 30 and c.business_days == (0, 1, 2, 3, 4)
    assert (c.open_time.hour, c.close_time.hour) == (8, 18)
    assert c.responder.default == "fast" and c.responder.personas["never"] is None
    assert c.responder.personas["ooo"].wall_hours == 72
    assert (c.fraud_first_hours, c.fraud_second_hours) == (2, 4)
    assert str(c.memo.assumed_rate) == "0.09" and c.memo.watch_min_grade == 3
    assert c.gates["credit_decision"].info == ("request_info",)
    assert c.vendor_check_products == ("Equipment Loan",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("quiet_minutes"),
        lambda r: r["business_hours"].update(open="25:99"),
        lambda r: r["business_hours"].update(open="19:00"),
        lambda r: r["issue_rules"]["requestable"].append("(unclosed"),
        lambda r: r["responder"].update(default="nobody"),
        lambda r: r["responder"]["answers"].update(credit_decision="maybe"),
        lambda r: r["responder"]["by_person"].update(x="nobody"),
        lambda r: r["gates"]["credit_decision"].update(advance=["bogus"]),
        lambda r: r["credit_memo"].update(assumed_rate="abc"),
        lambda r: r["responder"]["personas"].update(fast={"wall_hours": -1, "business_minutes": 1}),
        lambda r: r["deadline"].update(statistic="mean"),
    ],
)
def test_bad_values_are_config_errors(tmp_path, mutate):
    with pytest.raises(ConfigError):
        load_move_config(mutated(tmp_path, mutate))


def test_yaml_is_safe_loaded(tmp_path):
    p = tmp_path / "evil.yaml"
    p.write_text("quiet_minutes: !!python/object/apply:os.getcwd []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_move_config(p)


def test_env_selects_file_and_persona_at_load_time(tmp_path, monkeypatch):
    p = mutated(tmp_path, lambda r: r.update(quiet_minutes=5))
    monkeypatch.setenv("PRISM_MOVE_CONFIG", str(p))
    monkeypatch.setenv("PRISM_MOVE_PERSONA", "never")
    c = load_move_config()
    assert c.quiet_minutes == 5 and c.responder.default == "never"
    monkeypatch.setenv("PRISM_MOVE_PERSONA", "bogus")
    with pytest.raises(ConfigError):
        load_move_config()


def test_use_config_restores_the_previous_config():
    before = get_config()
    import dataclasses

    with use_config(dataclasses.replace(before, quiet_minutes=1)):
        assert get_config().quiet_minutes == 1
    assert get_config() is before


def test_event_cap_freezes_only_that_deal_through_the_engine_guard(tmp_path, monkeypatch):
    rules = make_rules(tmp_path)
    st = make_state()
    monkeypatch.setattr(move_stage, "MAX_EVENTS", 1)
    with pytest.raises(RuntimeError, match="events"):
        move_stage.step_deal(st, rules, at("2026-03-20T12:00"))
