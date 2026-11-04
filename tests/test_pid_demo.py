"""Lock in the behavior of examples/agent/pid_demo/PID.fmu and its scenarios.

PID.fmu wraps third-party source (pms67/PID, MIT licensed) with a hand-written
FMI 2.0 Co-Simulation shim (src/pid_fmi2.c). Every number here was read off an
actual run, not predicted -- see WORK_LOG.md. These tests exist so a rebuild
(examples/agent/pid_demo/build.sh) or an edit to pid_fmi2.c cannot silently
change what the seven example scenarios demonstrate.
"""
import json
from pathlib import Path

import pytest

from fmpy.scenario_agent import plan, review, run, run_many

ROOT = Path(__file__).parent.parent / "examples" / "agent" / "pid_demo"
FMU = ROOT / "PID.fmu"
SCENARIOS = ROOT / "scenarios"

pytestmark = pytest.mark.skipif(not FMU.is_file(), reason="PID.fmu is a build artifact; run build.sh")


def load(name):
    return json.loads((SCENARIOS / name).read_text(encoding="utf-8"))


def test_review_has_no_blocking_findings():
    """The hand-written declarations should be richer than the default export path."""
    reviewed = review(str(FMU))
    assert reviewed["ok"] and reviewed["runnable_by_scenario_runner"]
    assert reviewed["blocking"] == []
    codes = {f["code"] for f in reviewed["findings"]}
    assert codes <= {"missing_range", "missing_unit"}  # Kp/Ki/... have no declared bounds or units


def test_plan_derives_scenarios_from_the_declared_dependencies():
    planned = plan(str(FMU))
    assert planned["ok"]
    ids = {p["id"] for p in planned["proposals"]}
    assert ids == {"smoke", "step-setpoint", "step-measurement",
                   "dependency-coverage", "parameters-at-declared-bounds"}
    for proposal in planned["proposals"]:
        assert proposal["valid"], proposal.get("errors")


def test_01_step_response_shows_proportional_plus_integral_growth():
    result = run(str(FMU), load("01_step_response.json"))
    assert result["ok"] and result["passed"] is True
    output = result["report"]["summary"]["outputs"]["output"]
    assert output["first"] == 0.0
    assert 7.0 < output["max"] < 7.5  # proportional (6) plus a growing integral term
    assert output["max"] < 10.0  # nowhere near the output clamp


def test_02_measurement_step_produces_a_derivative_kick_that_decays():
    result = run(str(FMU), load("02_measurement_step_derivative_kick.json"))
    assert result["ok"]  # no checks: this scenario is meant to be read off the graph
    diff = result["report"]["summary"]["outputs"]["differentiator_state"]
    assert diff["max"] > 7.0  # the spike at the instant measurement changes
    assert abs(diff["last"]) < 1e-4  # decayed back toward 0 well before the run ends


def test_03_anti_windup_clamp_holds_the_integrator_at_its_declared_limit():
    result = run(str(FMU), load("03_anti_windup_clamp.json"))
    assert result["ok"] and result["passed"] is True
    integrator = result["report"]["summary"]["outputs"]["integrator_state"]
    assert integrator["max"] == pytest.approx(5.0)  # exactly limMaxInt, never beyond
    assert integrator["last"] == pytest.approx(5.0)  # stays clamped, does not settle back


def test_04_output_saturates_before_the_integrator_does():
    result = run(str(FMU), load("04_output_saturation.json"))
    assert result["ok"] and result["passed"] is True
    outputs = result["report"]["summary"]["outputs"]
    assert outputs["output"]["first"] == pytest.approx(10.0)  # clamped from the very first sample
    assert outputs["integrator_state"]["max"] < 5.0  # its own clamp has not engaged yet


def test_05_setpoint_step_alone_never_excites_the_derivative():
    result = run(str(FMU), load("05_setpoint_step_no_derivative_kick.json"))
    assert result["ok"] and result["passed"] is True
    diff = result["report"]["summary"]["outputs"]["differentiator_state"]
    assert diff["min"] == diff["max"] == pytest.approx(0.0)
    # The response still happens -- only the derivative term is unaffected.
    assert result["report"]["summary"]["outputs"]["output"]["max"] > 0


def test_06_a_sample_time_mismatch_is_reported_through_the_fmu_log():
    result = run(str(FMU), load("06_communication_step_mismatch_warns.json"))
    assert result["ok"] and result["passed"] is True
    summary = result["report"]["summary"]
    assert summary["log_warnings"] == summary["sample_count"] - 1  # every doStep after the first
    message = result["report"]["fmu_log"][0]["message"]
    assert "communicationStepSize" in message and "parameter T" in message


def test_07_invalid_anti_windup_bounds_are_refused_at_initialization():
    result = run(str(FMU), load("07_invalid_integrator_bounds_fails_init.json"))
    assert not result["ok"]
    error = result["errors"][0]
    assert error["code"] == "fmi_call_failed"
    assert "fmi2ExitInitializationMode" in error["message"]


def test_batch_runs_all_seven_scenarios_and_tallies_the_real_mix_of_outcomes():
    """02 has no checks (observational) and 07 is deliberately refused at init --
    see their own descriptions. The other five pass. This locks that mix in."""
    paths = sorted(SCENARIOS.glob("*.json"))
    assert len(paths) == 7
    envelope = run_many(str(FMU), paths)
    assert envelope["ok"]
    assert envelope["summary"] == {"total": 7, "passed": 5, "failed": 0, "no_verdict": 1,
                                   "error": 1}
    by_id = {entry["id"]: entry for entry in envelope["results"]}
    assert by_id["measurement_step_derivative_kick"]["passed"] is None
    assert by_id["invalid_integrator_bounds_fails_init"]["ok"] is False
    assert by_id["invalid_integrator_bounds_fails_init"]["errors"][0]["code"] == "fmi_call_failed"
    assert "실행 불가 1" in envelope["report"]


def test_batch_writes_a_results_folder_per_scenario_except_the_one_that_fails_to_run(tmp_path):
    paths = sorted(SCENARIOS.glob("*.json"))
    envelope = run_many(str(FMU), paths, output=tmp_path)
    for path in paths:
        stem = path.stem
        if "invalid_integrator_bounds" in stem:
            assert not (tmp_path / stem).exists()
        else:
            assert (tmp_path / stem / "report.json").is_file()
    assert (tmp_path / "summary.json").is_file()
