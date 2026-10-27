"""Scenario schema + validator: structure, predicate DSL, references, warnings, CLI."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from worldline.cli.validate import app as validate_app, register
from worldline.worlds.scenario import scenario_from_dict
from worldline.worlds.schema import (
    EXAMPLE_SCENARIO, SCHEMA_PATH, check_predicate, load_schema, scaffold, validate_data, validate_file,
    validate_text,
)

ROOT = Path(__file__).resolve().parents[1]
INVARIANTS = ROOT / "invariants"
SHIPPED = sorted(p for p in (ROOT / "scenarios").rglob("*.yaml"))


def base() -> dict:
    return yaml.safe_load(EXAMPLE_SCENARIO)


def errors(doc, **kw) -> list[str]:
    return validate_data(doc, **kw).messages("error")


def warnings(doc, **kw) -> list[str]:
    return validate_data(doc, **kw).messages("warning")


# ---------------------------------------------------------------- the schema file

def test_schema_file_is_draft_2020_12_and_refs_resolve():
    s = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(s["required"]) == {"id", "seed", "clock"}
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    for ref in {r.split('"')[0] for r in text.split('"$ref": "')[1:]}:
        node = s
        for part in ref[2:].split("/"):
            node = node[part]
        assert isinstance(node, dict)
    assert load_schema() == s


def test_schema_covers_every_dataclass_field():
    from dataclasses import fields
    from worldline.worlds.scenario import Actor, DecisionRule, DeliveryPolicy, Inject, Role, TruthTransition
    d = load_schema()["$defs"]
    for cls, key in [(Role, "role"), (DeliveryPolicy, "delivery"), (Actor, "actor"),
                     (DecisionRule, "decision_rule"), (Inject, "inject"), (TruthTransition, "transition")]:
        assert {f.name for f in fields(cls)} == set(d[key]["properties"]), key


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_shipped_scenarios_validate_clean(path):
    r = validate_file(path, invariants=INVARIANTS)
    assert r.ok, r.messages()
    assert r.warnings == [], r.messages()


def test_example_and_scaffold_validate_and_load():
    for text in (EXAMPLE_SCENARIO, scaffold("demo_x")):
        r = validate_text(text, invariants=INVARIANTS)
        assert r.ok and not r.warnings, r.messages()
        scenario_from_dict(yaml.safe_load(text))
    assert yaml.safe_load(scaffold("demo_x"))["id"] == "demo_x"


# ---------------------------------------------------------------- errors with paths and suggestions

def test_typo_in_bare_filter_suggests_payload_field():
    d = base()
    d["actors"][0]["decision_rules"][0]["when"] = "count(kind='report', severty>=3) >= 1"
    msgs = errors(d)
    assert any(m.startswith("actors[0].decision_rules[0].when: unknown filter 'severty' "
                            "(did you mean 'payload.severity'?)") for m in msgs), msgs


def test_typo_in_payload_filter_checked_against_injects():
    d = base()
    d["actors"][0]["decision_rules"][0]["when"] = "count(kind='report', payload.severty>=3) >= 1"
    msgs = errors(d)
    assert any("unknown filter 'payload.severty' (did you mean 'payload.severity'?)" in m for m in msgs), msgs


def test_payload_field_with_no_close_match_is_only_a_warning():
    d = base()
    d["actors"][0]["decision_rules"][0]["when"] = "count(kind='report', payload.zzz>=3) >= 1"
    assert errors(d) == []
    assert any("filter 'payload.zzz'" in m for m in warnings(d))


@pytest.mark.parametrize("when,needle", [
    ("cont(kind='report') >= 1", "unknown function 'cont' (did you mean 'count'?)"),
    ("count(kind='report', not_corected) >= 1", "unknown flag 'not_corected' (did you mean 'not_corrected'?)"),
    ("count(kind='report', within_ns=5s) >= 1", "within_ns and ack_kind are only valid inside acknowledged()"),
    ("count(kind='report' >= 1", "predicate syntax error"),
    ("count(kind='report') >=", "predicate syntax error"),
    ("", "must not be empty"),
])
def test_predicate_errors(when, needle):
    d = base()
    d["actors"][0]["decision_rules"][0]["when"] = when
    msgs = errors(d)
    assert any(m.startswith("actors[0].decision_rules[0].when:") and needle in m for m in msgs), msgs


def test_every_predicate_is_parsed_with_parse_predicate():
    # the same text the loader would reject is rejected here, with a path
    d = base()
    d["actors"][0]["decision_rules"][0]["when"] = "count(kind='report') >= 1 and"
    with pytest.raises(ValueError):
        scenario_from_dict(d)
    assert errors(d)


def test_check_predicate_standalone():
    assert check_predicate("exists(kind='x')", "w") == []
    assert check_predicate("exist(kind='x')", "w")[0].message.startswith("unknown function 'exist'")


@pytest.mark.parametrize("mutate,path,needle", [
    (lambda d: d["actors"][0].__setitem__("role", "operater"), "actors[0].role",
     "unknown role 'operater' (did you mean 'operator'?)"),
    (lambda d: d["injects"][1]["payload"].__setitem__("corrects", "report-01"), "injects[1].payload.corrects",
     "corrects 'report-01' is not an inject id (did you mean 'report-001'?)"),
    (lambda d: d["injects"].append(dict(d["injects"][0])), "injects[2].id", "duplicate inject id 'report-001'"),
    (lambda d: d["injects"][0].__setitem__("prority", 3), "injects[0].prority",
     "unknown key 'prority' (did you mean 'priority'?)"),
    (lambda d: d.__setitem__("injcts", []), "injcts", "unknown key 'injcts' (did you mean 'injects'?)"),
    (lambda d: d["roles"][0]["delivery"].__setitem__("ordering", "fifoo"), "roles[0].delivery.ordering",
     "invalid value 'fifoo' (did you mean 'fifo'?)"),
    (lambda d: d["roles"][0]["delivery"].__setitem__("drop_probability", 1.5), "roles[0].delivery.drop_probability",
     "must be <= 1"),
    (lambda d: d["roles"][0]["delivery"].__setitem__("jitter_ns", "-2s"), "roles[0].delivery.jitter_ns",
     "invalid value '-2s'"),
    (lambda d: d["injects"][0].__setitem__("at_ns", "60 sec"), "injects[0].at_ns", "invalid value '60 sec'"),
    (lambda d: d.pop("seed"), "", "missing required key 'seed'"),
    (lambda d: d["actors"][0]["decision_rules"][0].pop("otherwise"), "actors[0].decision_rules[0]",
     "missing required key 'otherwise'"),
    (lambda d: d.__setitem__("seed", "abc"), "seed", "expected an integer"),
    (lambda d: d["actors"][0]["decision_rules"][0].__setitem__("at_ns", "20m"), "actors[0].decision_rules[0].at_ns",
     "outside the horizon"),
    (lambda d: d.__setitem__("transitions", [{"id": "t", "kind": "outage", "from_ns": "5m", "to_ns": "1m"}]),
     "transitions[0].to_ns", "is before from_ns"),
    (lambda d: d.__setitem__("transitions", [{"id": "t", "kind": "blackout", "from_ns": 0, "to_ns": 1}]),
     "transitions[0].kind", "invalid value 'blackout'"),
    (lambda d: d.__setitem__("transitions", [{"id": "t", "kind": "outage", "from_ns": 0, "to_ns": "1m",
                                              "source_id": "sensor_a", "roles": ["operatr"]}]),
     "transitions[0].roles[0]", "unknown role 'operatr' (did you mean 'operator'?)"),
    (lambda d: d["roles"].append({"id": "operator"}), "roles[1].id", "duplicate role id 'operator'"),
    (lambda d: d["actors"].append(copy.deepcopy(d["actors"][0]) | {"id": "other"}),
     "actors[1].decision_rules[0].id", "duplicate decision rule id 'act_or_wait'"),
])
def test_structural_and_reference_errors(mutate, path, needle):
    d = base()
    mutate(d)
    r = validate_data(d)
    hits = [i for i in r.errors if i.path == path and needle in i.message]
    assert hits, r.messages()


def test_auto_generated_inject_ids_collide_with_explicit_ones():
    d = base()
    d["injects"] = [{"at_ns": 1, "source_id": "s", "kind": "report"},
                    {"id": "report-001", "at_ns": 2, "source_id": "s", "kind": "report"}]
    assert any("duplicate inject id 'report-001'" in m for m in errors(d))


def test_invariant_ids_checked_only_when_directory_given():
    d = base()
    d["invariants"] = ["decision.consistant_with_truth"]
    assert errors(d) == []
    msgs = errors(d, invariants=INVARIANTS)
    assert msgs == ["invariants[0]: unknown invariant 'decision.consistant_with_truth' "
                    "(did you mean 'decision.consistent_with_truth'?)"]
    assert errors(d, invariants=["decision.consistant_with_truth"]) == []
    assert any("does not exist" in m for m in errors(base(), invariants=ROOT / "no_such_dir"))


def test_yaml_level_errors_and_line_numbers():
    r = validate_text("id: x\nseed: 1\nseed: 2\nclock: {horizon_ns: 1m}\n")
    dup = [i for i in r.errors if "duplicate key 'seed'" in i.message]
    assert dup and dup[0].line == 3 and dup[0].path == "seed"
    r = validate_text("id: [unclosed\n")
    assert not r.ok and r.errors[0].message.startswith("YAML syntax error") and r.errors[0].line
    r = validate_text("- just\n- a list\n")
    assert not r.ok and "must be a YAML mapping" in r.errors[0].message
    text = EXAMPLE_SCENARIO.replace("role: operator", "role: operater")
    r = validate_text(text)
    line = next(i.line for i in r.errors if i.path == "actors[0].role")
    assert text.splitlines()[line - 1].strip() == "role: operater"


def test_validator_agrees_with_loader_on_mutations():
    """Whatever the validator accepts, the loader loads; whatever the loader rejects, the validator rejects."""
    muts = [
        lambda d: d["clock"].pop("horizon_ns"),
        lambda d: d["roles"][0]["delivery"].__setitem__("ordering", "lifo"),
        lambda d: d["actors"][0].__setitem__("role", "nobody"),
        lambda d: d["actors"][0]["decision_rules"][0].__setitem__("when", "count(kind=) >= 1"),
        lambda d: d["actors"][0]["decision_rules"][0].__setitem__("window_ns", "-1s"),
        lambda d: d["actors"][0]["decision_rules"][0].__setitem__("at_ns", "1h"),
        lambda d: d["injects"].append(dict(d["injects"][0])),
        lambda d: d.__setitem__("transitions", [{"id": "t", "kind": "outage", "from_ns": 9, "to_ns": 1}]),
        lambda d: d["roles"].append({"id": "operator"}),
        lambda d: d["injects"][0].__setitem__("priority", 2),
        lambda d: d.__setitem__("specimen", False),
        lambda d: d.__setitem__("provenance", {"source": "unit test"}),
    ]
    for m in muts:
        d = base()
        m(d)
        ok = validate_data(d).ok
        try:
            scenario_from_dict(copy.deepcopy(d))
            loads = True
        except Exception:  # noqa: BLE001
            loads = False
        if not loads:
            assert not ok, d
        if ok:
            assert loads, d


# ---------------------------------------------------------------- warnings

def test_warnings_for_suspicious_scenarios():
    d = base()
    for inj in d["injects"]:
        inj["policy_labels"] = ["ops"]          # an inject without labels is visible to every role
    d["roles"].append({"id": "blind", "visibility": ["secret"]})
    d["actors"][0]["decision_rules"][0]["when"] = "exists(kind='reprot') >= 1"
    d["injects"].append({"id": "late-001", "at_ns": "11m", "source_id": "sensor_a", "kind": "report",
                       "policy_labels": ["ops"]})
    d["transitions"] = [{"id": "t", "kind": "outage", "from_ns": "1m", "to_ns": "2m", "source_id": "sensor_b"}]
    r = validate_data(d)
    assert r.ok, r.messages()
    w = r.messages("warning")
    expect = [
        "roles[1]: role 'blind' never receives anything",
        "roles[1].visibility[0]: label 'secret' is never carried by any inject",
        "actors[0].decision_rules[0].when: exists(...) is a boolean",
        "actors[0].decision_rules[0].when: kind 'reprot' never occurs in injects (did you mean 'report'?)",
        "injects[2].at_ns: inject 'late-001' at 660s is after the horizon",
        "transitions[0].source_id: no inject comes from source 'sensor_b' (did you mean 'sensor_a'?)",
    ]
    for e in expect:
        assert any(m.startswith(e) for m in w), (e, w)


def test_warning_actor_role_cannot_see_counted_kind():
    d = base()
    d["roles"][0]["visibility"] = ["public"]
    for inj in d["injects"]:
        inj["policy_labels"] = ["secret"]
    d["injects"].append({"id": "pub-001", "at_ns": 1, "source_id": "s", "kind": "status", "policy_labels": ["public"]})
    w = warnings(d)
    assert any("counts kind 'report' but its role 'operator' cannot see any 'report' inject" in m for m in w), w


def test_warning_no_rules_and_window_empty():
    d = base()
    d["actors"] = []
    assert any(m.startswith("actors: no decision rules") for m in warnings(d))
    d = base()
    d["actors"][0]["decision_rules"][0]["window_ns"] = "10s"
    assert any("no inject of kind report happens inside the decision window" in m for m in warnings(d))


def test_specimen_requires_header_comment():
    text = "specimen: true\n" + EXAMPLE_SCENARIO
    assert any("SPECIMEN" in m for m in validate_text(text).messages("warning"))
    text = "# SPECIMEN: seeded defect.\nspecimen: true\n" + EXAMPLE_SCENARIO
    assert validate_text(text).warnings == []


# ---------------------------------------------------------------- CLI

runner = CliRunner()


def cli():
    parent = typer.Typer()

    @parent.command("noop")
    def _noop() -> None:  # a second command keeps the parent a group, as in worldline.cli.main
        pass

    register(parent)
    return parent


def test_cli_registers_top_level_commands():
    names = {c.name for c in typer.main.get_command(cli()).commands.values()}
    assert {"validate", "init"} <= names
    assert {c.name for c in validate_app.registered_commands} == {"validate", "init"}


def test_cli_validate_ok_and_fail(tmp_path):
    r = runner.invoke(cli(), ["validate", str(ROOT / "scenarios" / "port_alert.yaml"),
                              "--invariants", str(INVARIANTS)])
    assert r.exit_code == 0, r.output
    assert "OK" in r.output
    bad = tmp_path / "bad.yaml"
    bad.write_text(EXAMPLE_SCENARIO.replace("payload.severity>=3", "severty>=3"), encoding="utf-8")
    r = runner.invoke(cli(), ["validate", str(bad), "--invariants", str(INVARIANTS)])
    assert r.exit_code == 1
    assert "actors[0].decision_rules[0].when: unknown filter 'severty' (did you mean 'payload.severity'?)" in \
        r.output.replace("\n", "")
    r = runner.invoke(cli(), ["validate", str(bad), "--json"])
    assert r.exit_code == 1
    doc = json.loads(r.output)
    assert doc[0]["ok"] is False and doc[0]["issues"][0]["line"] == 16   # the `when:` line


def test_cli_strict_turns_warnings_into_failure(tmp_path):
    p = tmp_path / "warn.yaml"
    p.write_text(EXAMPLE_SCENARIO.replace("kind='report'", "kind='reprot'"), encoding="utf-8")
    assert runner.invoke(cli(), ["validate", str(p)]).exit_code == 0
    assert runner.invoke(cli(), ["validate", str(p), "--strict"]).exit_code == 1


def test_cli_example_and_usage():
    r = runner.invoke(cli(), ["validate", "--example"])
    assert r.exit_code == 0
    assert validate_text(r.output, invariants=INVARIANTS).ok
    assert runner.invoke(cli(), ["validate"]).exit_code == 2


def test_cli_init_scaffolds_valid_template(tmp_path):
    p = tmp_path / "sub" / "clinic_queue.yaml"
    r = runner.invoke(cli(), ["init", str(p)])
    assert r.exit_code == 0, r.output
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["id"] == "clinic_queue"
    assert runner.invoke(cli(), ["validate", str(p), "--strict", "--invariants", str(INVARIANTS)]).exit_code == 0
    assert runner.invoke(cli(), ["init", str(p)]).exit_code == 1          # no silent overwrite
    assert runner.invoke(cli(), ["init", str(p), "--force", "--id", "q2"]).exit_code == 0
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["id"] == "q2"
    assert runner.invoke(cli(), ["init", str(tmp_path / "x.yaml"), "--id", "bad id!"]).exit_code == 2
