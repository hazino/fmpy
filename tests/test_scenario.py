from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from fmpy.scenario import run_scenario, validate_scenario


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
