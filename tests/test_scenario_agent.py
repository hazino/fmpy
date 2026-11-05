import json
from copy import deepcopy

import pytest

from fmpy.scenario_agent import (TOOLS, authoring_context, call, describe_fmu, format_batch_report,
                                 main, plan, review, run, run_many, schema, structure, validate)
from fmpy.scenario_schema import ERROR_CODES, scenario_schema


@pytest.fixture(scope="module")
def feedthrough(reference_fmus_dist_dir):
    return str(reference_fmus_dist_dir / "2.0" / "Feedthrough.fmu")


def good():
    return {"version": 1, "name": "demo", "stop_time": 1.0, "step_size": 0.1,
            "initial": {"Float64_continuous_input": 0.0},
            "events": [{"id": "step", "time": 0.2, "set": {"Float64_continuous_input": 2.0}}],
            "outputs": ["Float64_continuous_output"],
            "checks": [{"id": "band", "variable": "Float64_continuous_output",
                        "min": 0.0, "max": 2.0}]}


def test_tools_manifest_is_callable_and_self_describing():
    envelope = call("tools")
    assert envelope["ok"] and envelope["tools"] == TOOLS
    for tool in TOOLS:
        assert set(tool) == {"name", "description", "parameters"}
        assert tool["parameters"]["type"] == "object"
        assert tool["parameters"]["additionalProperties"] is False
        assert call(tool["name"]) is not None  # dispatchable without raising


def test_unknown_tool_and_bad_arguments_come_back_as_data():
    unknown = call("simulate")
    assert not unknown["ok"] and unknown["errors"][0]["code"] == "unknown_tool"
    assert "run" in unknown["errors"][0]["available"]
    bad = call("validate", fmu="x.fmu", nonsense=1)
    assert not bad["ok"] and bad["errors"][0]["code"] == "bad_arguments"


def test_unreadable_fmu_is_reported_not_raised(tmp_path):
    envelope = describe_fmu(tmp_path / "missing.fmu")
    assert not envelope["ok"] and envelope["errors"][0]["code"] == "fmu_unreadable"


def test_describe_separates_what_a_scenario_may_read_and_write(feedthrough):
    described = describe_fmu(feedthrough)
    assert described["ok"] and described["supported"]
    assert described["fmi_version"] == "2.0" and described["model_name"] == "Feedthrough"
    # Parameters are settable before initialization only.
    assert "Float64_fixed_parameter" in described["writable_initial"]
    assert "Float64_fixed_parameter" not in described["writable_runtime"]
    assert "Float64_continuous_input" in described["writable_runtime"]
    # Outputs are never writable and the timestamp is not a variable to record.
    assert "Float64_continuous_output" not in described["writable_initial"]
    assert "time" not in described["readable"]
    assert "String_output" in described["readable"]
    assert "String_output" not in described["check_candidates"]
    assert described["default_experiment"]["stopTime"] == 2.0
    assert all(isinstance(v["min"], (float, type(None))) for v in described["variables"])


def test_context_is_one_self_contained_grounding(feedthrough):
    context = authoring_context(feedthrough)
    assert context["ok"]
    assert set(context) >= {"interface", "schema", "capabilities", "error_codes", "example",
                            "instructions"}
    assert context["error_codes"] == ERROR_CODES
    assert len(json.dumps(context)) < 100_000  # fits in a prompt


def test_context_example_is_accepted_and_runs(feedthrough):
    example = authoring_context(feedthrough)["example"]
    assert validate(feedthrough, example)["ok"]
    assert run(feedthrough, example)["ok"]


def test_validate_reports_every_problem_at_its_json_path(feedthrough):
    scenario = {"version": 1, "stop_time": 1.0, "step_size": 0.1,
                "initial": {"Float64_continuous_inpt": 0.0, "Float64_continuous_output": 1.0},
                "events": [{"time": 0.15, "set": {"Int32_input": 2}},
                           {"time": 0.5, "set": {"Boolean_input": 1}}],
                "rules": [{"when": {"variable": "Boolean_output", "op": ">", "value": True},
                           "stop": True}],
                "outputs": ["Float64_continuous_output", "Float64_continuous_output"],
                "checks": [{"variable": "String_output", "min": 0}]}
    envelope = validate(feedthrough, scenario)
    assert not envelope["ok"]
    found = {(e["code"], e["path"]) for e in envelope["errors"]}
    assert found == {
        ("unknown_variable", "initial.Float64_continuous_inpt"),
        ("not_writable", "initial.Float64_continuous_output"),
        ("duplicate_output", "outputs[1]"),
        ("off_grid", "events[0].time"),
        ("type_mismatch", "events[1].set.Boolean_input"),
        ("unsupported_operator", "rules[0].when.op"),
        ("invalid_check", "checks[0].variable"),
    }
    assert all(e["code"] in ERROR_CODES for e in envelope["errors"])
    misspelled = next(e for e in envelope["errors"] if e["code"] == "unknown_variable")
    assert "Float64_continuous_input" in misspelled["candidates"]


def test_validate_rejects_a_scenario_that_is_not_an_object(feedthrough):
    envelope = validate(feedthrough, [{"version": 1}])
    assert not envelope["ok"] and envelope["errors"][0]["code"] == "not_an_object"


def test_validate_accepts_and_reports_the_grid(feedthrough):
    envelope = validate(feedthrough, good())
    assert envelope["ok"] and envelope["steps"] == 10 and envelope["samples"] == 11
    assert envelope["has_checks"]


def test_run_returns_a_verdict_and_a_summary_without_the_table(feedthrough, tmp_path):
    envelope = run(feedthrough, good(), output=tmp_path / "run", include_samples=3)
    assert envelope["ok"] and envelope["passed"] is True
    assert "samples" not in envelope["report"]
    assert envelope["sample_count"] == 11
    assert len(envelope["samples"]) == 3 and envelope["samples_truncated"]
    summary = envelope["report"]["summary"]
    assert summary["termination"] == "stop_time" and summary["failed_checks"] == []
    assert summary["outputs"]["Float64_continuous_output"]["max"] == 2.0
    for artifact in ("results_csv", "report_json", "scenario_json"):
        assert (tmp_path / "run").exists() and envelope["artifacts"][artifact]
    written = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert written["summary"] == summary


def test_run_reports_a_failed_test_as_a_successful_call(feedthrough):
    failing = good()
    failing["checks"][0]["max"] = 1.0
    envelope = run(feedthrough, failing)
    assert envelope["ok"] is True and envelope["passed"] is False
    assert envelope["report"]["summary"]["failed_checks"] == ["Float64_continuous_output"]
    assert envelope["artifacts"] == {}


def test_run_refuses_an_invalid_scenario_before_touching_the_fmu(feedthrough):
    broken = good()
    broken["outputs"] = ["nope"]
    envelope = run(feedthrough, broken)
    assert not envelope["ok"] and envelope["errors"][0]["code"] == "unknown_variable"


def test_cli_writes_one_json_object_and_signals_through_the_exit_code(
        feedthrough, tmp_path, capsys):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(good()), encoding="utf-8")

    assert main(["run", "--fmu", feedthrough, "--scenario", str(path),
                 "--output", str(tmp_path / "out")]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    failing = good()
    failing["checks"][0]["max"] = 1.0
    path.write_text(json.dumps(failing), encoding="utf-8")
    assert main(["run", "--fmu", feedthrough, "--scenario", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False

    broken = good()
    broken["outputs"] = ["nope"]
    path.write_text(json.dumps(broken), encoding="utf-8")
    assert main(["validate", "--fmu", feedthrough, "--scenario", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False

    path.write_text("{not json", encoding="utf-8")
    assert main(["validate", "--fmu", feedthrough, "--scenario", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "scenario_unreadable"

    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_id"] == schema()["schema_id"]


def test_cli_requires_the_arguments_each_tool_needs(capsys):
    with pytest.raises(SystemExit):
        main(["validate", "--fmu", "model.fmu"])


SHAPE_ERRORS = [
    {"version": 2},
    {"unknown_option": True},
    {"outputs": []},
    {"outputs": ["Float64_continuous_output", "Float64_continuous_output"]},
    {"rules": [{"when": {"variable": "Float64_continuous_output", "op": ">", "value": 1}}]},
    {"rules": [{"when": {"variable": "Float64_continuous_output", "op": "~", "value": 1},
                "stop": True}]},
    {"checks": [{"variable": "Float64_continuous_output"}]},
    {"events": [{"time": 0.2}]},
]


@pytest.mark.parametrize("change", SHAPE_ERRORS)
def test_schema_and_runner_agree_on_malformed_scenarios(feedthrough, change):
    """The published schema must not accept what the runner rejects, or vice versa."""
    jsonschema = pytest.importorskip("jsonschema")
    scenario = {**good(), **change}
    assert not validate(feedthrough, scenario)["ok"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(scenario, scenario_schema())


def test_schema_accepts_what_the_runner_accepts(feedthrough, root_dir):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(scenario_schema())
    for scenario in [good(), authoring_context(feedthrough)["example"]]:
        jsonschema.validate(scenario, scenario_schema())
    for name in ["examples/demo/scenario.json", "examples/scenarios/feedthrough.json"]:
        stored = json.loads((root_dir / name).read_text(encoding="utf-8-sig"))
        jsonschema.validate(stored, scenario_schema())
        assert validate(feedthrough, deepcopy(stored))["ok"], name


def test_structure_reports_the_declared_dependency_graph(feedthrough):
    reported = structure(feedthrough)
    assert reported["ok"]
    declared = reported["structure"]
    assert declared["dependencies"]["Int32_output"]["inputs"] == ["Int32_input"]
    assert declared["inputs_to_outputs"]["String_input"] == ["Boolean_output", "String_output"]
    assert declared["inputs_without_declared_effect"] == []
    assert declared["co_simulation"]["model_identifier"] == "Feedthrough"
    assert [c["name"] for c in declared["log_categories"]] == ["logEvents", "logStatusError"]
    option, = declared["type_definitions"]
    assert [item["name"] for item in option["items"]] == ["Option 1", "Option 2"]
    assert any(name.startswith("sources/") for name in declared["archive"]["source_files"])
    assert "documentation/index.html" in declared["archive"]["documentation"]


def test_structure_distinguishes_no_dependency_from_an_undeclared_one(reference_fmus_dist_dir):
    """An empty list means 'driven by state'; null means the exporter said nothing."""
    declared = structure(reference_fmus_dist_dir / "2.0" / "BouncingBall.fmu")["structure"]
    assert declared["dependencies"]["h"]["depends_on"] == []
    assert declared["continuous_states"] == 2 and declared["event_indicators"] == 1


@pytest.mark.parametrize("name", ["Feedthrough", "BouncingBall", "Stair"])
def test_every_proposal_is_valid_and_actually_runs(reference_fmus_dist_dir, name):
    fmu = str(reference_fmus_dist_dir / "2.0" / f"{name}.fmu")
    planned = plan(fmu)
    assert planned["ok"] and planned["proposals"]
    for proposal in planned["proposals"]:
        assert proposal["valid"], (proposal["id"], proposal.get("errors"))
        executed = run(fmu, proposal["scenario"])
        assert executed["ok"], (proposal["id"], executed.get("errors"))
        assert executed["sample_count"] > 1


def test_a_plan_proposes_stimulus_but_never_a_verdict(feedthrough):
    planned = plan(feedthrough)
    ids = [proposal["id"] for proposal in planned["proposals"]]
    assert ids[0] == "smoke" and "step-Int32_input" in ids and "dependency-coverage" in ids
    for proposal in planned["proposals"]:
        assert "checks" not in proposal["scenario"]
        assert proposal["needs_from_domain"]
        assert run(feedthrough, proposal["scenario"])["passed"] is None
    assert any("does not state what counts as a pass" in note for note in planned["notes"])
    # A step proposal records the input it drives and the outputs that declare it.
    stepped = next(p for p in planned["proposals"] if p["id"] == "step-Int32_input")
    assert stepped["scenario"]["outputs"] == ["Int32_input", "Int32_output"]
    assert stepped["scenario"]["events"][0]["set"] == {"Int32_input": 1}


def test_a_plan_keeps_every_proposal_on_one_grid(feedthrough):
    planned = plan(feedthrough)
    grid = planned["grid"]
    assert grid["steps"] * grid["step_size"] == pytest.approx(grid["stop_time"])
    for proposal in planned["proposals"]:
        scenario = proposal["scenario"]
        assert (scenario["step_size"], scenario["stop_time"]) == (grid["step_size"],
                                                                  grid["stop_time"])


def test_a_plan_refuses_an_fmu_the_runner_cannot_execute(reference_fmus_dist_dir):
    planned = plan(reference_fmus_dist_dir / "3.0" / "Feedthrough.fmu")
    assert not planned["ok"] and planned["errors"][0]["code"] == "unsupported_fmu"


def test_plan_and_structure_are_reachable_from_the_command_line(feedthrough, capsys):
    assert main(["plan", "--fmu", feedthrough]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["proposals"][0]["id"] == "smoke"
    assert main(["structure", "--fmu", feedthrough]) == 0
    assert "dependencies" in json.loads(capsys.readouterr().out)["structure"]


def test_review_names_the_missing_declarations_and_their_cost(feedthrough):
    reviewed = review(feedthrough)
    assert reviewed["ok"] and reviewed["runnable_by_scenario_runner"]
    assert reviewed["blocking"] == []
    by_code = {f["code"]: f for f in reviewed["findings"]}
    # Feedthrough declares no units, no ranges and no descriptions.
    assert "Float64_continuous_input" in by_code["missing_range"]["affects"]
    assert "Float64_continuous_input" in by_code["missing_unit"]["affects"]
    assert by_code["missing_default_experiment"]["affects"] == ["stepSize"]
    # It does declare its dependencies, so that is not a finding.
    assert "undeclared_dependencies" not in by_code
    assert "inputs_without_declared_effect" not in by_code
    for found in reviewed["findings"]:
        assert found["effect"] and found["fix"]
        assert found["severity"] in ("blocking", "high", "medium", "low")
    assert any("acceptance criteria" in note for note in reviewed["notes"])


def test_review_sorts_blocking_problems_first_and_plan_refuses_them(tmp_path):
    """An FMU the runner cannot execute is reported as blocking, not as advice."""
    from fmpy.template import create_fmu, generate_model_description

    built = tmp_path / "Template.fmu"
    create_fmu(generate_model_description(n_parameters=1, n_inputs=1, n_outputs=1, n_states=1),
               built)
    reviewed = review(built)
    assert reviewed["ok"] and not reviewed["runnable_by_scenario_runner"]
    assert reviewed["findings"][0]["severity"] == "blocking"
    assert "unsupported_interface" in reviewed["blocking"]
    assert not plan(built)["ok"]


def test_review_flags_an_input_nothing_declares_a_dependency_on(root_dir):
    reviewed = review(root_dir / "tests" / "resources" / "Drivetrain.fmu")
    by_code = {f["code"]: f for f in reviewed["findings"]}
    assert by_code["inputs_without_declared_effect"]["affects"] == ["tau"]
    # Its variables carry declared types with units, so that is not a finding.
    assert "missing_unit" not in by_code


def test_review_is_reachable_from_the_command_line(feedthrough, capsys):
    assert main(["review", "--fmu", feedthrough]) == 0
    assert "findings" in json.loads(capsys.readouterr().out)


def write_scenario(path, scenario):
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return path


def test_run_many_counts_every_outcome_and_keeps_going_past_a_bad_one(feedthrough, tmp_path):
    passing = write_scenario(tmp_path / "01_passing.json", good())
    failing_scenario = good()
    failing_scenario["checks"][0]["max"] = 1.0
    failing = write_scenario(tmp_path / "02_failing.json", failing_scenario)
    no_verdict_scenario = {k: v for k, v in good().items() if k != "checks"}
    no_verdict = write_scenario(tmp_path / "03_no_verdict.json", no_verdict_scenario)
    broken_scenario = good()
    broken_scenario["outputs"] = ["nope"]
    broken = write_scenario(tmp_path / "04_broken.json", broken_scenario)
    missing = tmp_path / "05_missing.json"  # never written

    seen = []
    envelope = run_many(feedthrough, [passing, failing, no_verdict, broken, missing],
                        on_progress=lambda i, e: seen.append((i, e["id"])))
    assert envelope["ok"]
    assert envelope["summary"] == {"total": 5, "passed": 1, "failed": 1, "no_verdict": 1,
                                   "error": 2}
    # Every scenario built from good() keeps its "name": "demo"; only the
    # unreadable file falls back to its filename stem.
    assert seen == [(0, "demo"), (1, "demo"), (2, "demo"), (3, "demo"), (4, "05_missing")]
    by_path = {entry["path"]: entry for entry in envelope["results"]}
    assert by_path[str(passing)]["passed"] is True
    assert by_path[str(failing)]["passed"] is False
    assert by_path[str(no_verdict)]["passed"] is None and by_path[str(no_verdict)]["ok"]
    assert by_path[str(broken)]["ok"] is False
    assert by_path[str(broken)]["errors"][0]["code"] == "unknown_variable"
    assert by_path[str(missing)]["errors"][0]["code"] == "scenario_unreadable"
    assert all("samples" not in entry for entry in envelope["results"])


def test_run_many_writes_one_subdirectory_per_scenario_plus_a_summary(feedthrough, tmp_path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    write_scenario(scenario_dir / "a.json", good())
    write_scenario(scenario_dir / "b.json", good())
    output = tmp_path / "out"

    envelope = run_many(feedthrough, sorted(scenario_dir.glob("*.json")), output=str(output))
    assert envelope["summary"]["passed"] == 2
    assert (output / "a" / "report.json").is_file()
    assert (output / "b" / "results.csv").is_file()
    written = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert written["summary"] == envelope["summary"]


def test_run_many_cancellation_stops_before_touching_the_remaining_files(feedthrough, tmp_path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    write_scenario(scenario_dir / "a.json", good())
    write_scenario(scenario_dir / "b.json", good())
    paths = sorted(scenario_dir.glob("*.json"))
    finished = {"n": 0}

    def on_progress(index, entry):
        finished["n"] += 1

    def cancelled():
        # cancelled() is now also checked once per communication step inside
        # the first scenario's own run (see the next test), so counting
        # *every* call would trip this well before "a" finishes. Only ask
        # for the cutoff once a whole scenario has actually completed.
        return finished["n"] >= 1

    envelope = run_many(feedthrough, paths, on_progress=on_progress, cancelled=cancelled)
    assert envelope["summary"] == {"total": 2, "passed": 1, "failed": 0, "no_verdict": 0,
                                   "error": 1}
    assert envelope["results"][1]["errors"][0]["code"] == "cancelled"


def test_run_many_cancellation_stops_a_scenario_already_in_progress(feedthrough, tmp_path):
    """cancelled() is forwarded into run_scenario() itself (via run()), checked
    once per communication step, so a long-running scenario can be cut off
    partway -- not just skipped outright before it starts."""
    scenario = good()
    scenario["stop_time"] = 1000.0  # many steps at this step_size; not instant
    path = write_scenario(tmp_path / "long.json", scenario)
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 3  # a few steps in, well before stop_time

    envelope = run_many(feedthrough, [path], cancelled=cancelled)
    assert envelope["summary"] == {"total": 1, "passed": 0, "failed": 0, "no_verdict": 0,
                                   "error": 1}
    assert envelope["results"][0]["errors"][0]["code"] == "cancelled"


def test_run_many_refuses_an_unreadable_fmu_up_front(tmp_path):
    envelope = run_many(tmp_path / "missing.fmu", [])
    assert not envelope["ok"] and envelope["errors"][0]["code"] == "fmu_unreadable"


def test_format_batch_report_is_one_line_per_scenario_plus_counts():
    summary = {"total": 2, "passed": 1, "failed": 1, "no_verdict": 0, "error": 0}
    results = [{"id": "a", "ok": True, "passed": True},
              {"id": "b", "ok": True, "passed": False,
               "report": {"summary": {"failed_checks": ["y"]}}}]
    text = format_batch_report(summary, results)
    assert text.splitlines()[0] == "2개 중 합격 1 · 불합격 1 · 판정 기준 없음 0 · 실행 불가 0"
    assert "| a | 합격 |  |" in text
    assert "| b | 불합격 | 실패한 검사: y |" in text


def test_batch_tool_is_reachable_through_call_and_tools_manifest(feedthrough, tmp_path):
    write_scenario(tmp_path / "a.json", good())
    assert any(t["name"] == "batch" for t in TOOLS)
    envelope = call("batch", fmu=feedthrough, scenario_paths=[str(tmp_path / "a.json")])
    assert envelope["ok"] and envelope["summary"]["passed"] == 1


def test_cli_batch_expands_a_directory_reports_and_signals_through_exit_code(
        feedthrough, tmp_path, capsys):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    write_scenario(scenario_dir / "a.json", good())
    failing_scenario = good()
    failing_scenario["checks"][0]["max"] = 1.0
    write_scenario(scenario_dir / "b.json", failing_scenario)

    assert main(["batch", "--fmu", feedthrough, "--scenarios", str(scenario_dir)]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["summary"] == {"total": 2, "passed": 1, "failed": 1, "no_verdict": 0,
                                   "error": 0}

    assert main(["batch", "--fmu", feedthrough, "--scenarios", str(scenario_dir),
                 "--report"]) == 1
    report = capsys.readouterr().out
    assert report.startswith("2개 중 합격 1")

    write_scenario(scenario_dir / "c.json", {"not": "a scenario"})
    assert main(["batch", "--fmu", feedthrough, "--scenarios", str(scenario_dir)]) == 2
