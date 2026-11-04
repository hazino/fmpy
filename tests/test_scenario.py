from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from fmpy.scenario import (LogCapture, MAX_LOG_ENTRIES, ScenarioValidationError, _callbacks,
                           run_scenario, validate_scenario)


def description():
    return NS(fmiVersion="2.0", coSimulation=NS(), modelVariables=[
        NS(name="u", type="Real", causality="input", variability="continuous", min=0, max=10),
        NS(name="y", type="Real", causality="output", variability="continuous"),
        NS(name="enabled", type="Boolean", causality="input", variability="discrete"),
    ])


def scenario():
    return {"version": 1, "stop_time": 1.0, "step_size": 0.1, "outputs": ["y"]}


@pytest.mark.parametrize("change", [
    {"initial": {"missing": 1}}, {"initial": {"y": 1}},
    {"initial": {"u": 11}}, {"initial": {"u": True}},
    {"initial": {"enabled": 1}}, {"step_size": 0},
    {"stop_time": float("nan")}, {"stop_time": 1.05},
    {"events": [{"time": 0.15, "set": {"u": 1}}]},
    {"events": [{"time": 1, "set": {"u": 1}}]},
    {"events": [{"time": 0.2, "set": {"u": 1}}, {"time": 0.2, "set": {"u": 2}}]},
    {"checks": [{"variable": "y", "min": 2, "max": 1}]},
    {"unknown_option": True}, {"rules": [{"when": {"variable": "y", "op": ">", "value": 1}}]},
])
def test_reject_invalid(change):
    data = scenario()
    data.update(change)
    with pytest.raises(ValueError):
        validate_scenario(data, description())


def test_reject_other_fmi():
    md = description()
    md.fmiVersion = "3.0"
    with pytest.raises(ValueError, match="FMI 2.0"):
        validate_scenario(scenario(), md)


def test_valid():
    assert validate_scenario(scenario(), description())[1] == 10


def test_real_fmu(reference_fmus_dist_dir):
    data = {"version": 1, "stop_time": 1, "step_size": 0.1,
            "initial": {"Float64_continuous_input": 0.0, "Boolean_input": False, "String_input": "hello"},
            "events": [{"time": 0.2, "set": {"Float64_continuous_input": 2.0, "Boolean_input": True}}],
            "outputs": ["Float64_continuous_output", "Boolean_output", "String_output"],
            "checks": [{"variable": "Float64_continuous_output", "min": 0, "max": 2}]}
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    result = run_scenario(path, data)
    assert result["passed"]
    assert len(result["samples"]) == 11
    assert result["samples"][2]["Float64_continuous_output"] == 0
    assert result["samples"][3]["Float64_continuous_output"] == 2
    assert result["samples"][3]["Boolean_output"]
    assert result["samples"][3]["String_output"] == "hello"
    failed = deepcopy(data)
    failed["checks"][0]["max"] = 1
    assert run_scenario(path, failed)["passed"] is False
    data["rules"] = [{"when": {"variable": "Float64_continuous_output", "op": ">=", "value": 2}, "stop": True}]
    stopped = run_scenario(path, data)
    assert stopped["termination"] == "rule"
    assert stopped["samples"][-1]["time"] < 1
    assert stopped["fired_rules"] == [0]


def test_rule_action(reference_fmus_dist_dir):
    data = {"version": 1, "stop_time": 0.3, "step_size": 0.1,
            "initial": {"Int32_input": 1}, "outputs": ["Int32_output"],
            "rules": [{"when": {"variable": "Int32_output", "op": "==", "value": 1},
                       "set": {"Int32_input": 3}}]}
    result = run_scenario(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu", data)
    assert result["samples"][-1]["Int32_output"] == 3
    assert result["fired_rules"] == [0]
    assert result["passed"] is None


def test_errors_carry_a_code_and_a_json_path():
    data = scenario()
    data["events"] = [{"time": 0.2, "set": {"u": 99}}]
    with pytest.raises(ScenarioValidationError) as raised:
        validate_scenario(data, description())
    error, = raised.value.errors
    assert (error.code, error.path) == ("out_of_range", "events[0].set.u")
    assert error.as_dict()["max"] == 10.0


def test_one_call_reports_every_independent_problem():
    data = scenario()
    data.update(initial={"u": 11, "enabled": 1},
                outputs=["y", "y", "nope"],
                events=[{"time": 0.15, "set": {"u": 1}}],
                checks=[{"variable": "enabled", "min": 0}])
    with pytest.raises(ScenarioValidationError) as raised:
        validate_scenario(data, description())
    assert {(e.code, e.path) for e in raised.value.errors} == {
        ("out_of_range", "initial.u"),
        ("type_mismatch", "initial.enabled"),
        ("duplicate_output", "outputs[1]"),
        ("unknown_variable", "outputs[2]"),
        ("off_grid", "events[0].time"),
        ("invalid_check", "checks[0].variable"),
    }
    assert "and 5 more problem(s)" in str(raised.value)


def test_labels_are_optional_and_must_be_unique():
    data = scenario()
    data.update(name="case", description="why", metadata={"trace": "abc"},
                events=[{"id": "a", "time": 0.1, "set": {"u": 1}},
                        {"id": "a", "time": 0.2, "set": {"u": 2}}])
    with pytest.raises(ScenarioValidationError) as raised:
        validate_scenario(data, description())
    assert [(e.code, e.path) for e in raised.value.errors] == [("duplicate_id", "events[1].id")]
    data["events"][1]["id"] = "b"
    assert validate_scenario(data, description())[1] == 10


def test_captured_log_is_bounded_and_timestamped():
    log = LogCapture(limit=2)
    log.time = 1.5
    for status in (1, 3, 4):
        log(None, b"scenario", status, b"logAll", b"message")
    assert [entry["status"] for entry in log.entries] == ["WARNING", "ERROR"]
    assert log.entries[0] == {"time": 1.5, "status": "WARNING", "category": "logAll",
                              "message": "message"}
    assert log.dropped == 1
    assert log.count("ERROR", "FATAL") == 1
    assert LogCapture().limit == MAX_LOG_ENTRIES


def test_the_fmus_own_messages_reach_the_capture():
    """The variadic message is formatted by a native proxy, not by Python."""
    log = LogCapture()
    log.time = 0.25
    _callbacks(log).logger(None, b"scenario", 3, b"logStatusError", b"step rejected")
    assert log.entries == [{"time": 0.25, "status": "ERROR", "category": "logStatusError",
                            "message": "step rejected"}]


def test_summary_describes_each_output(reference_fmus_dist_dir):
    data = {"version": 1, "stop_time": 1.0, "step_size": 0.1,
            "initial": {"Float64_continuous_input": 1.0, "Boolean_input": False,
                        "String_input": "idle"},
            "events": [{"time": 0.5, "set": {"Boolean_input": True, "String_input": "busy"}}],
            "outputs": ["Float64_continuous_output", "Boolean_output", "String_output"],
            "checks": [{"variable": "Float64_continuous_output", "min": 0.0, "max": 2.0}]}
    result = run_scenario(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu", data)

    summary = result["summary"]
    assert summary["sample_count"] == 11 and summary["planned_steps"] == 10
    assert summary["termination"] == "stop_time" and summary["passed"] is True
    assert summary["failed_checks"] == [] and summary["log_errors"] == 0

    real = summary["outputs"]["Float64_continuous_output"]
    assert (real["min"], real["max"], real["first"], real["last"]) == (1.0, 1.0, 1.0, 1.0)
    assert real["type"] == "Real" and real["nonfinite_count"] == 0

    boolean = summary["outputs"]["Boolean_output"]
    assert boolean["first"] is False and boolean["last"] is True
    assert boolean["change_count"] == 1 and boolean["true_count"] == 5
    assert boolean["changes"][-1] == {"time": result["samples"][6]["time"], "value": True}

    text = summary["outputs"]["String_output"]
    assert text["distinct"] == ["idle", "busy"] and text["distinct_count"] == 2


def test_booleans_are_reported_as_json_booleans(reference_fmus_dist_dir):
    data = {"version": 1, "stop_time": 0.2, "step_size": 0.1,
            "initial": {"Boolean_input": True}, "outputs": ["Boolean_output"]}
    result = run_scenario(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu", data)
    assert all(row["Boolean_output"] is True for row in result["samples"])


def test_checks_report_the_remaining_room(reference_fmus_dist_dir):
    data = {"version": 1, "stop_time": 0.3, "step_size": 0.1,
            "initial": {"Float64_continuous_input": 2.0},
            "outputs": ["Float64_continuous_output"],
            "checks": [{"variable": "Float64_continuous_output", "min": 0.0, "max": 5.0}]}
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    passing, = run_scenario(path, data)["checks"]
    assert passing["passed"] and passing["margin"] == 2.0 and passing["failure_count"] == 0
    assert passing["worst"] == 2.0 and passing["first_failure_time"] is None

    data["checks"][0]["max"] = 1.0
    failing, = run_scenario(path, data)["checks"]
    assert not failing["passed"] and failing["margin"] == -1.0
    assert failing["failure_count"] == 4 and failing["first_failure_time"] == 0.0


def test_a_fired_rule_records_why_it_fired(reference_fmus_dist_dir):
    data = {"version": 1, "stop_time": 0.5, "step_size": 0.1,
            "initial": {"Float64_continuous_input": 3.0},
            "outputs": ["Float64_continuous_output"],
            "rules": [{"id": "trip", "description": "shut down above 2",
                       "when": {"variable": "Float64_continuous_output", "op": ">", "value": 2.0},
                       "set": {"Float64_continuous_input": 0.0}, "stop": True}]}
    result = run_scenario(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu", data)
    assert result["termination"] == "rule" and result["fired_rules"] == [0]
    event, = result["rule_events"]
    assert event["id"] == "trip" and event["index"] == 0 and event["time"] == 0.0
    assert event["trigger"]["actual"] == 3.0 and event["stop"] is True
    assert event["set"] == {"Float64_continuous_input": 0.0}


def test_an_fmu_that_asks_to_stop_ends_the_run_with_its_results(reference_fmus_dist_dir):
    """Stair signals termination by discarding a step; that is an outcome, not a crash."""
    data = {"version": 1, "stop_time": 10.0, "step_size": 0.2, "outputs": ["counter"],
            "checks": [{"variable": "counter", "min": 1, "max": 10}]}
    result = run_scenario(reference_fmus_dist_dir / "2.0" / "Stair.fmu", data)
    assert result["termination"] == "fmu_terminated"
    assert result["passed"] is True
    assert result["samples"][-1] == {"time": 9.0, "counter": 10}
    assert result["summary"]["end_time"] == 9.0
