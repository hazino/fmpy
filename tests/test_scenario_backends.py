"""fmpy.scenario_backends: the execution-and-analysis agent (and the shared
generation-agent plumbing it was factored out of).

This is deliberately a second agent, not the same one as the generation path
(generate_with_ollama/run_with_claude): it never writes a scenario, only runs
ones that already exist and, at most, describes what happened in words. The
Ollama-backed tests are skipped where no server with a pulled model is
reachable (this machine has one: Ollama 0.40.4, gemma:2b) and are not
mocked -- see docs/agent-integration-ko.md for what a real run showed.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from fmpy.scenario_backends import (_analysis_prompt, analyze_results_with_claude,
                                    analyze_results_with_ollama, analyze_with_claude,
                                    analyze_with_ollama, ollama_models)

PID_ROOT = Path(__file__).parent.parent / "examples" / "agent" / "pid_demo"
PID_FMU = PID_ROOT / "PID.fmu"
PID_SCENARIOS = PID_ROOT / "scenarios"


def ollama_ready():
    return bool(ollama_models())


def write(path, scenario):
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return path


def good():
    return {"version": 1, "name": "demo", "stop_time": 1.0, "step_size": 0.1,
           "initial": {"Float64_continuous_input": 0.0},
           "events": [{"id": "step", "time": 0.2, "set": {"Float64_continuous_input": 2.0}}],
           "outputs": ["Float64_continuous_output"],
           "checks": [{"id": "band", "variable": "Float64_continuous_output",
                      "min": 0.0, "max": 2.0}]}


def test_analysis_prompt_uses_exactly_the_data_the_markdown_report_uses():
    summary = {"total": 2, "passed": 1, "failed": 0, "no_verdict": 1, "error": 0}
    results = [
        {"id": "a", "ok": True, "passed": True, "report": {"summary": {"failed_checks": []}}},
        {"id": "b", "ok": True, "passed": None, "report": {"summary": {"failed_checks": []}}},
    ]
    prompt = _analysis_prompt("model.fmu", summary, results)
    assert "model.fmu" in prompt
    assert "2개 중 합격 1" in prompt
    assert "| a | 합격 |" in prompt
    assert "| b | 판정 기준 없음 |" in prompt
    assert "새로 계산하거나 추측하지" in prompt  # told not to invent numbers


def test_analysis_prompt_includes_failed_checks_and_run_errors():
    summary = {"total": 2, "passed": 0, "failed": 1, "no_verdict": 0, "error": 1}
    results = [
        {"id": "a", "ok": True, "passed": False,
         "report": {"summary": {"failed_checks": ["y"]}}},
        {"id": "b", "ok": False, "errors": [{"message": "boom"}]},
    ]
    prompt = _analysis_prompt("model.fmu", summary, results)
    assert "| a | 불합격 | 실패한 검사: y |" in prompt
    assert "| b | 오류 | boom |" in prompt


def test_analyze_with_ollama_refuses_an_empty_folder(tmp_path):
    envelope = analyze_with_ollama("model.fmu", tmp_path)
    assert not envelope["ok"] and envelope["errors"][0]["code"] == "no_scenarios_found"


def test_analyze_with_claude_refuses_an_empty_folder_without_needing_the_package(tmp_path):
    # Fails before ever importing anthropic -- no package required for this path.
    envelope = analyze_with_claude("model.fmu", tmp_path)
    assert not envelope["ok"] and envelope["errors"][0]["code"] == "no_scenarios_found"


@pytest.mark.skipif(importlib.util.find_spec("anthropic") is not None,
                    reason="anthropic is installed; cannot exercise the missing-dependency path")
def test_analyze_with_claude_raises_import_error_without_the_package(
        reference_fmus_dist_dir, tmp_path):
    write(tmp_path / "a.json", good())
    with pytest.raises(ImportError):
        analyze_with_claude(str(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"), tmp_path)


@pytest.mark.skipif(importlib.util.find_spec("anthropic") is not None,
                    reason="anthropic is installed; cannot exercise the missing-dependency path")
def test_analyze_results_with_claude_raises_import_error_without_the_package():
    with pytest.raises(ImportError):
        analyze_results_with_claude("model.fmu", {"total": 0}, [])


def test_analyze_with_ollama_degrades_gracefully_when_the_model_is_unreachable(
        reference_fmus_dist_dir, tmp_path):
    """Execution is independent of the model: an unreachable server loses the
    analysis, not the (already-correct) batch result."""
    write(tmp_path / "a.json", good())
    envelope = analyze_with_ollama(str(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"),
                                   tmp_path, host="http://localhost:1")
    assert envelope["ok"] and envelope["summary"]["passed"] == 1
    assert envelope["analysis"] is None
    assert "analysis_error" in envelope and envelope["analysis_error"]


def test_analyze_with_ollama_respects_cancellation(reference_fmus_dist_dir, tmp_path):
    write(tmp_path / "a.json", good())
    write(tmp_path / "b.json", good())
    envelope = analyze_with_ollama(str(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"),
                                   tmp_path, cancelled=lambda: True)
    assert envelope["ok"]  # run_many itself always reports ok; the entries record the skip
    assert envelope["analysis"] is None
    assert all(not entry["ok"] for entry in envelope["results"])


@pytest.mark.skipif(not ollama_ready(), reason="no Ollama server with a pulled model reachable")
def test_analyze_results_with_ollama_describes_a_small_hand_built_batch():
    summary = {"total": 1, "passed": 1, "failed": 0, "no_verdict": 0, "error": 0}
    results = [{"id": "smoke_test", "ok": True, "passed": True,
               "report": {"summary": {"failed_checks": []}}}]
    text, error = analyze_results_with_ollama("model.fmu", summary, results)
    assert error is None
    assert text and len(text) > 0


@pytest.mark.skipif(not ollama_ready(), reason="no Ollama server with a pulled model reachable")
def test_analyze_with_ollama_runs_every_scenario_before_describing_them(
        reference_fmus_dist_dir, tmp_path):
    path = reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu"
    write(tmp_path / "a.json", good())
    seen = []
    envelope = analyze_with_ollama(str(path), tmp_path,
                                   on_progress=lambda i, e: seen.append(e["id"]))
    assert envelope["ok"] and envelope["summary"]["passed"] == 1
    assert seen == ["demo"]  # run_many's on_progress fired during execution
    assert envelope["analysis"] is not None  # the model actually answered


@pytest.mark.skipif(not PID_FMU.is_file(), reason="PID.fmu is a build artifact; run build.sh")
@pytest.mark.skipif(not ollama_ready(), reason="no Ollama server with a pulled model reachable")
def test_analyze_with_ollama_on_the_real_pid_demo_folder_gets_the_tally_right():
    """The execution and the count are the parts that must be exactly right;
    see test_pid_demo.py for the same ground truth via run_many directly.
    The model's own prose is not checked here -- a 2B model's summary of this
    exact table was observed to miscount which scenarios passed (see
    docs/agent-integration-ko.md); that is a model-quality limit, not a
    pipeline bug, and this test does not pretend otherwise."""
    envelope = analyze_with_ollama(str(PID_FMU), str(PID_SCENARIOS))
    assert envelope["ok"]
    assert envelope["summary"] == {"total": 7, "passed": 5, "failed": 0, "no_verdict": 1,
                                   "error": 1}
    assert envelope["analysis"] is not None and len(envelope["analysis"]) > 0
