"""JSON-in/JSON-out tool surface for FMI 2.0 scenarios.

This is the seam an automated caller plugs into. Every function takes and
returns plain JSON-serializable data, reports problems as data instead of
raising, and never interprets natural language or executes supplied code. The
same functions are exposed as a command line that writes exactly one JSON
object to stdout::

    python -m fmpy.scenario_agent tools
    python -m fmpy.scenario_agent plan     --fmu model.fmu
    python -m fmpy.scenario_agent review   --fmu model.fmu
    python -m fmpy.scenario_agent context  --fmu model.fmu
    python -m fmpy.scenario_agent validate --fmu model.fmu --scenario s.json
    python -m fmpy.scenario_agent run      --fmu model.fmu --scenario - --output out
    python -m fmpy.scenario_agent batch    --fmu model.fmu --scenarios scenarios/ --output out --report

Exit codes: 0 the tool succeeded, 1 the test failed (``passed`` is false),
2 the tool reported problems (``ok`` is false).

A language model is given ``context`` (interface, schema, capabilities), writes
a scenario, and is corrected by ``validate`` until it is accepted. Nothing in
this module requires a model to be present.
"""

import argparse
import json
import sys
from pathlib import Path

from fmpy import read_model_description
from fmpy.fmi1 import FMICallException
from fmpy.scenario import (MAX_STEPS, NUMERIC, TYPES, ScenarioCancelled,
                           ScenarioValidationError, run_scenario, validate_scenario,
                           write_results)
from fmpy.scenario_plan import model_structure, propose_scenarios, review_declaration
from fmpy.scenario_schema import CAPABILITIES, ERROR_CODES, SCHEMA_ID, scenario_schema

VARIABLE_FIELDS = ("name", "type", "causality", "variability", "unit", "description",
                   "start", "min", "max")


def _ok(tool, **result):
    return {"ok": True, "tool": tool, **result}


def _fail(tool, code, message, path="", **extra):
    return {"ok": False, "tool": tool,
            "errors": [{"code": code, "path": path, "message": message, **extra}]}


def _float(value):
    """Model description literals may be strings; report numbers as numbers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _variable(v):
    entry = {}
    for key in VARIABLE_FIELDS:
        value = getattr(v, key, None)
        if key in ("min", "max") and value is None:
            value = getattr(getattr(v, "declaredType", None), key, None)
        entry[key] = _float(value) if key in ("min", "max") and value is not None else value
    return entry


def _model_description(tool, fmu):
    """Read a model description, or return the envelope explaining why not."""
    try:
        return read_model_description(str(fmu)), None
    except Exception as error:
        return None, _fail(tool, "fmu_unreadable", f"Cannot read the FMU: {error}", "fmu",
                           fmu=str(fmu))


def describe_fmu(fmu, tool="describe"):
    """Report the FMU's interface, split into what a scenario may read and write."""
    md, failure = _model_description(tool, fmu)
    if failure:
        return failure
    # 'independent' is the simulation time itself, which the result table already
    # carries as its reserved 'time' column.
    usable = [v for v in md.modelVariables
              if v.type in TYPES and v.causality != "independent" and v.name != "time"]
    writable_initial = [v.name for v in usable if v.causality == "input" or (
        v.causality == "parameter" and v.variability in ("fixed", "tunable"))]
    writable_runtime = [v.name for v in usable if v.causality == "input"]
    experiment = md.defaultExperiment
    return _ok(tool,
               fmu=str(fmu),
               model_name=md.modelName,
               fmi_version=md.fmiVersion,
               co_simulation=md.coSimulation is not None,
               supported=md.fmiVersion == "2.0" and md.coSimulation is not None,
               description=md.description,
               default_experiment={k: _float(getattr(experiment, k, None)) for k in
                                   ("startTime", "stopTime", "tolerance", "stepSize")}
               if experiment is not None else None,
               writable_initial=writable_initial,
               writable_runtime=writable_runtime,
               readable=[v.name for v in usable],
               check_candidates=[v.name for v in usable if v.type in NUMERIC],
               unsupported_variables=[v.name for v in md.modelVariables if v.type not in TYPES],
               variables=[_variable(v) for v in usable])


def structure(fmu):
    """Report the execution structure the FMU declares: dependencies, states, types."""
    tool = "structure"
    md, failure = _model_description(tool, fmu)
    if failure:
        return failure
    return _ok(tool, fmu=str(fmu), structure=model_structure(md, fmu))


def review(fmu):
    """Report which declarations are missing from the FMU and what each one costs.

    Useful to whoever owns the exporter: the richer the model description, the
    better the scenarios that can be derived from it.
    """
    tool = "review"
    md, failure = _model_description(tool, fmu)
    if failure:
        return failure
    return _ok(tool, fmu=str(fmu), **review_declaration(md, fmu))


def plan(fmu, validate_proposals=True):
    """Derive scenarios worth running from what the FMU declares about itself.

    Each proposal is a complete scenario plus why it is worth running and what a
    domain source still has to supply. Nothing here invents acceptance criteria.
    """
    tool = "plan"
    md, failure = _model_description(tool, fmu)
    if failure:
        return failure
    if md.fmiVersion != "2.0" or md.coSimulation is None:
        return _fail(tool, "unsupported_fmu", "Only FMI 2.0 Co-Simulation is supported",
                     fmi_version=md.fmiVersion, co_simulation=md.coSimulation is not None)
    derived = propose_scenarios(md, fmu)
    if validate_proposals:
        for proposal in derived["proposals"]:
            checked = validate(fmu, proposal["scenario"], tool=tool)
            proposal["valid"] = checked["ok"]
            if not checked["ok"]:
                proposal["errors"] = checked["errors"]
    return _ok(tool, fmu=str(fmu), **derived)


def authoring_context(fmu):
    """Everything needed to write a valid scenario for this FMU, in one object.

    Hand this to a generator as its grounding: the interface it may address, the
    schema it must satisfy, what the runner can do, and the error codes it will
    get back.
    """
    tool = "context"
    interface = describe_fmu(fmu, tool=tool)
    if not interface["ok"]:
        return interface
    return _ok(tool, interface={k: v for k, v in interface.items() if k not in ("ok", "tool")},
               schema=scenario_schema(), schema_id=SCHEMA_ID, capabilities=CAPABILITIES,
               error_codes=ERROR_CODES,
               example=_example(interface),
               instructions=[
                   "Emit one JSON object that validates against 'schema'. Emit nothing else.",
                   "Use only names from interface.writable_initial in 'initial', only names from "
                   "interface.writable_runtime in any 'set', and only names from "
                   "interface.readable in 'outputs' and rule conditions.",
                   "Every variable named in 'checks' must also appear in 'outputs'.",
                   "Keep stop_time and every event time on the step_size grid.",
                   "Record the intent in 'name', 'description' and per-item 'id' fields: the "
                   "report refers back to them.",
                   "Submit the scenario to the 'validate' tool and repair it using the returned "
                   "'code' and 'path' until it is accepted.",
               ])


def _example(interface):
    """A minimal scenario for this FMU, so a generator starts from working JSON."""
    numeric = [v for v in interface["variables"]
               if v["type"] in NUMERIC and v["name"] in interface["writable_runtime"]]
    readable = [v for v in interface["variables"] if v["type"] in NUMERIC]
    if not numeric or not readable:
        return None
    driven = next((v for v in numeric if v["type"] == "Real"), numeric[0])
    observed = next((v["name"] for v in readable if v["causality"] == "output"), readable[0]["name"])

    def settable(value):
        """Keep the example inside whatever the variable declares."""
        for limit, pick in (("min", max), ("max", min)):
            if driven[limit] is not None:
                value = pick(value, driven[limit])
        return float(value) if driven["type"] == "Real" else int(value)

    name = driven["name"]
    return {"version": 1, "name": "smoke test", "description": "one step change, one range check",
            "start_time": 0.0, "stop_time": 1.0, "step_size": 0.1,
            "initial": {name: settable(0)},
            "events": [{"id": "step", "time": 0.2, "set": {name: settable(1)}}],
            "outputs": [observed],
            "checks": [{"id": "band", "variable": observed, "min": -1000, "max": 1000}]}


def validate(fmu, scenario, tool="validate"):
    """Check a scenario against the FMU without instantiating native code."""
    md, failure = _model_description(tool, fmu)
    if failure:
        return failure
    if not isinstance(scenario, dict):
        return _fail(tool, "not_an_object",
                     f"The scenario must be a JSON object, got {type(scenario).__name__}")
    try:
        _, steps = validate_scenario(scenario, md)
    except ScenarioValidationError as error:
        return {"ok": False, "tool": tool, **error.as_dict()}
    return _ok(tool, steps=steps, samples=steps + 1,
               start_time=scenario.get("start_time", 0.0),
               stop_time=scenario["stop_time"], step_size=scenario["step_size"],
               outputs=list(scenario["outputs"]),
               has_checks=bool(scenario.get("checks")), max_steps=MAX_STEPS)


def run(fmu, scenario, output=None, include_samples=0, debug_logging=False):
    """Validate and execute a scenario. Return the report without the sample table.

    ``output`` writes results.csv, report.json and scenario.json into that
    directory. ``include_samples`` inlines at most that many recorded rows, for
    a caller that wants to look at a short trace directly.
    """
    tool = "run"
    checked = validate(fmu, scenario, tool=tool)
    if not checked["ok"]:
        return checked
    try:
        result = run_scenario(fmu, scenario, debug_logging=debug_logging)
    except ScenarioCancelled as error:
        return _fail(tool, "cancelled", str(error))
    except FMICallException as error:
        return _fail(tool, "fmi_call_failed", str(error),
                     hint="The FMU rejected a call. See 'fmu_log' of a previous run or "
                          "rerun with debug_logging for the FMU's own messages.")
    except Exception as error:
        return _fail(tool, "run_failed", f"{type(error).__name__}: {error}")
    report = {k: v for k, v in result.items() if k != "samples"}
    artifacts = {}
    if output is not None:
        write_results(output, scenario, result)
        directory = Path(output)
        artifacts = {"directory": str(directory),
                     "results_csv": str(directory / "results.csv"),
                     "report_json": str(directory / "report.json"),
                     "scenario_json": str(directory / "scenario.json")}
    envelope = _ok(tool, passed=result["passed"], report=report, artifacts=artifacts,
                   sample_count=len(result["samples"]))
    if include_samples:
        envelope["samples"] = result["samples"][:include_samples]
        envelope["samples_truncated"] = len(result["samples"]) > include_samples
    return envelope


def _summarize_batch(results):
    """Count outcomes across a batch: passed / failed / no verdict / could not run."""
    counts = {"total": len(results), "passed": 0, "failed": 0, "no_verdict": 0, "error": 0}
    for entry in results:
        if not entry.get("ok"):
            counts["error"] += 1
        elif entry.get("passed") is True:
            counts["passed"] += 1
        elif entry.get("passed") is False:
            counts["failed"] += 1
        else:
            counts["no_verdict"] += 1
    return counts


def run_many(fmu, scenario_paths, output=None, include_samples=0, debug_logging=False,
            on_progress=None, cancelled=None):
    """Validate and run several scenario files against the same FMU. One summary.

    Each path is read, validated and run independently -- one bad or failing
    scenario does not stop the rest. ``output``, given, gets one subdirectory
    per scenario (named after its file stem) plus a ``summary.json`` with the
    counts and every per-scenario result (without sample tables).

    ``on_progress(index, entry)``, if given, is called after each scenario
    with its 0-based position and its result entry, for a caller that wants to
    show progress before the whole batch finishes. ``cancelled()``, checked
    before each scenario, stops the remaining ones without running them.
    """
    tool = "batch"
    md, failure = _model_description(tool, fmu)
    if failure:
        return failure

    results = []
    for index, path in enumerate(scenario_paths):
        path = Path(path)
        entry = {"id": path.stem, "path": str(path)}
        if cancelled is not None and cancelled():
            entry.update(ok=False, passed=None,
                        errors=[{"code": "cancelled",
                                "message": "Skipped: the batch was cancelled."}])
            results.append(entry)
            if on_progress is not None:
                on_progress(index, entry)
            continue
        try:
            scenario = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            entry.update(ok=False, passed=None,
                        errors=[{"code": "scenario_unreadable", "message": str(error)}])
            results.append(entry)
            if on_progress is not None:
                on_progress(index, entry)
            continue
        entry["id"] = scenario.get("name") or path.stem
        scenario_output = str(Path(output) / path.stem) if output is not None else None
        outcome = run(fmu, scenario, output=scenario_output, include_samples=include_samples,
                     debug_logging=debug_logging)
        entry.update(outcome)
        results.append(entry)
        if on_progress is not None:
            on_progress(index, entry)

    summary = _summarize_batch(results)
    light_results = [{k: v for k, v in entry.items() if k != "samples"} for entry in results]
    if output is not None:
        out_dir = Path(output)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.json").write_text(
            json.dumps({"summary": summary, "results": light_results}, indent=2,
                      ensure_ascii=False), encoding="utf-8")
    return _ok(tool, fmu=str(fmu), summary=summary, results=light_results,
               report=format_batch_report(summary, light_results))


def format_batch_report(summary, results):
    """A short Markdown table: one line per scenario, counts up top."""
    lines = [f"{summary['total']}개 중 합격 {summary['passed']} · 불합격 {summary['failed']} · "
            f"판정 기준 없음 {summary['no_verdict']} · 실행 불가 {summary['error']}",
            "", "| 시나리오 | 결과 | 비고 |", "|---|---|---|"]
    for entry in results:
        if not entry.get("ok"):
            verdict = "중지" if entry["errors"][0]["code"] == "cancelled" else "오류"
            note = entry["errors"][0]["message"]
        else:
            passed = entry.get("passed")
            verdict = "합격" if passed is True else "불합격" if passed is False else "판정 기준 없음"
            failed = ((entry.get("report") or {}).get("summary") or {}).get("failed_checks") or []
            note = f"실패한 검사: {', '.join(failed)}" if failed else ""
        lines.append(f"| {entry['id']} | {verdict} | {note} |")
    return "\n".join(lines)


def schema():
    """Return the scenario JSON Schema and the runner's capabilities."""
    return _ok("schema", schema=scenario_schema(), schema_id=SCHEMA_ID,
               capabilities=CAPABILITIES, error_codes=ERROR_CODES)


#: Callable tools, described the way a function-calling runtime expects.
TOOLS = [
    {"name": "context",
     "description": "Everything needed to write a scenario for one FMU: interface, JSON Schema, "
                    "capabilities, error codes and a working example. Call this first.",
     "parameters": {"type": "object", "required": ["fmu"], "additionalProperties": False,
                    "properties": {"fmu": {"type": "string", "description": "Path to the .fmu"}}}},
    {"name": "describe",
     "description": "The FMU's interface only: which variables a scenario may read and write.",
     "parameters": {"type": "object", "required": ["fmu"], "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"}}}},
    {"name": "structure",
     "description": "What the FMU declares about its own execution: which inputs each output "
                    "depends on, how many states and event indicators it has, its declared "
                    "types and units, and what else its archive ships.",
     "parameters": {"type": "object", "required": ["fmu"], "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"}}}},
    {"name": "plan",
     "description": "Derive scenarios worth running from the declared structure: a smoke test, "
                    "one step per input over the outputs it drives, a coverage sweep and the "
                    "declared parameter bounds. Each proposal is a validated scenario plus what "
                    "a domain source still has to supply. It invents no acceptance criteria.",
     "parameters": {"type": "object", "required": ["fmu"], "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"},
                                   "validate_proposals": {"type": "boolean", "default": True}}}},
    {"name": "review",
     "description": "Report which declarations the FMU is missing and what each one costs when "
                    "deriving scenarios. For whoever owns the exporter: a richer model "
                    "description yields better tests.",
     "parameters": {"type": "object", "required": ["fmu"], "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"}}}},
    {"name": "schema",
     "description": "The scenario JSON Schema, the runner's capabilities and the error codes.",
     "parameters": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "validate",
     "description": "Check a scenario against an FMU without running it. Returns every problem "
                    "with a code and a JSON path so one round trip is enough to repair it.",
     "parameters": {"type": "object", "required": ["fmu", "scenario"],
                    "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"},
                                   "scenario": {"type": "object",
                                                "description": "The scenario to check"}}}},
    {"name": "run",
     "description": "Validate and execute a scenario. Returns the verdict, a per-variable "
                    "summary, which rules fired and the FMU's log messages, but not the full "
                    "sample table.",
     "parameters": {"type": "object", "required": ["fmu", "scenario"],
                    "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"},
                                   "scenario": {"type": "object"},
                                   "output": {"type": "string",
                                              "description": "Directory for results.csv, "
                                                             "report.json and scenario.json"},
                                   "include_samples": {"type": "integer", "minimum": 0,
                                                       "description": "Inline at most this many "
                                                                      "recorded rows"},
                                   "debug_logging": {"type": "boolean"}}}},
    {"name": "batch",
     "description": "Validate and run several scenario files against the same FMU. Returns a "
                    "count of passed/failed/no-verdict/could-not-run and a short Markdown "
                    "report, one line per scenario; one bad scenario does not stop the rest.",
     "parameters": {"type": "object", "required": ["fmu", "scenario_paths"],
                    "additionalProperties": False,
                    "properties": {"fmu": {"type": "string"},
                                   "scenario_paths": {"type": "array",
                                                      "items": {"type": "string"},
                                                      "description": "Paths to scenario JSON files"},
                                   "output": {"type": "string",
                                              "description": "Directory for one subfolder per "
                                                             "scenario plus summary.json"},
                                   "include_samples": {"type": "integer", "minimum": 0},
                                   "debug_logging": {"type": "boolean"}}}},
]

_DISPATCH = {"context": authoring_context, "describe": describe_fmu, "structure": structure,
             "plan": plan, "review": review, "schema": schema, "validate": validate,
             "run": run, "batch": run_many}


def call(tool, **arguments):
    """Invoke one tool by name. Unknown names and bad arguments come back as data."""
    if tool == "tools":
        return _ok("tools", tools=TOOLS)
    if tool not in _DISPATCH:
        return _fail(tool, "unknown_tool", f"Unknown tool: {tool}",
                     available=["tools", *sorted(_DISPATCH)])
    try:
        return _DISPATCH[tool](**arguments)
    except TypeError as error:
        return _fail(tool, "bad_arguments", str(error))


def _read_scenario(source):
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8-sig")
    return json.loads(text)


def _expand_scenario_paths(paths):
    """A directory argument expands to its *.json files, sorted; a file passes through."""
    expanded = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            expanded.extend(sorted(path.glob("*.json")))
        else:
            expanded.append(path)
    return [str(p) for p in expanded]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tool", choices=["tools", "schema", "describe", "structure", "plan",
                                         "review", "context", "validate", "run", "batch"])
    parser.add_argument("--fmu")
    parser.add_argument("--scenario", help="Path to the scenario JSON, or - for stdin")
    parser.add_argument("--scenarios", nargs="+",
                        help="batch: scenario JSON files, or directories to glob *.json from")
    parser.add_argument("--output", help="Directory to write results into")
    parser.add_argument("--samples", type=int, default=0,
                        help="Inline at most this many recorded rows")
    parser.add_argument("--debug-logging", action="store_true")
    parser.add_argument("--report", action="store_true",
                        help="batch: print the Markdown report instead of the JSON envelope")
    args = parser.parse_args(argv)

    arguments = {}
    if args.tool in ("describe", "structure", "plan", "review", "context", "validate",
                     "run", "batch"):
        if not args.fmu:
            parser.error(f"{args.tool} requires --fmu")
        arguments["fmu"] = args.fmu
    if args.tool in ("validate", "run"):
        if not args.scenario:
            parser.error(f"{args.tool} requires --scenario")
        try:
            arguments["scenario"] = _read_scenario(args.scenario)
        except (OSError, ValueError) as error:
            envelope = _fail(args.tool, "scenario_unreadable", str(error), "scenario")
            print(json.dumps(envelope, ensure_ascii=False, indent=2))
            return 2
    if args.tool == "run":
        arguments.update(output=args.output, include_samples=args.samples,
                         debug_logging=args.debug_logging)
    if args.tool == "batch":
        if not args.scenarios:
            parser.error("batch requires --scenarios")
        arguments.update(scenario_paths=_expand_scenario_paths(args.scenarios),
                         output=args.output, include_samples=args.samples,
                         debug_logging=args.debug_logging)

    envelope = call(args.tool, **arguments)
    if args.tool == "batch" and args.report and envelope["ok"]:
        print(envelope["report"])
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    if not envelope["ok"]:
        return 2
    if args.tool == "batch":
        return 2 if envelope["summary"]["error"] else (1 if envelope["summary"]["failed"] else 0)
    return 1 if envelope.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
