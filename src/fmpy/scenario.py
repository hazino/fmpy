"""Deterministic JSON scenarios for FMI 2.0 Co-Simulation.

Run with ``python -m fmpy.scenario --help``. No AI runtime is required.
"""

import argparse
import csv
import json
import math
import operator
from pathlib import Path
from tempfile import TemporaryDirectory

from fmpy import extract, read_model_description
from fmpy.fmi2 import FMU2Slave


OPS = {"==": operator.eq, "!=": operator.ne, ">": operator.gt,
       ">=": operator.ge, "<": operator.lt, "<=": operator.le}
TYPES = {"Real", "Integer", "Enumeration", "Boolean", "String"}


def fields(obj, allowed, required=()):
    if not isinstance(obj, dict):
        raise ValueError("Expected an object")
    if set(obj) - set(allowed) or set(required) - set(obj):
        raise ValueError(f"Invalid fields: {list(obj)}; allowed={allowed}, required={required}")


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"Expected a finite number, got {value!r}")
    return value


def validate_scenario(scenario, md):
    """Validate before instantiating native FMU code. Return variables and step count."""
    fields(scenario, ("version", "start_time", "stop_time", "step_size", "initial",
                      "events", "rules", "outputs", "checks"),
           ("version", "stop_time", "step_size", "outputs"))
    if type(scenario["version"]) is not int or scenario["version"] != 1:
        raise ValueError("Scenario version must be 1")
    if md.fmiVersion != "2.0" or md.coSimulation is None:
        raise ValueError("Only FMI 2.0 Co-Simulation is supported")
    variables = {v.name: v for v in md.modelVariables}
    start = number(scenario.get("start_time", 0.0))
    stop = number(scenario["stop_time"])
    step = number(scenario["step_size"])
    if step <= 0 or stop <= start:
        raise ValueError("Require step_size > 0 and stop_time > start_time")

    def tick(time):
        ratio = (number(time) - start) / step
        if not math.isfinite(ratio) or not math.isclose(ratio, round(ratio), abs_tol=1e-8, rel_tol=0):
            raise ValueError("Times must lie on the fixed communication grid")
        return round(ratio)

    count = tick(stop)
    if not 1 <= count <= 1_000_000:
        raise ValueError("Scenario must contain 1 to 1,000,000 steps")

    def variable(name):
        if name not in variables:
            raise ValueError(f"Unknown variable: {name}")
        v = variables[name]
        if v.type not in TYPES:
            raise ValueError(f"Unsupported type for {name}: {v.type}")
        return v

    def value(v, x):
        if v.type == "Real":
            number(x)
        elif v.type in ("Integer", "Enumeration"):
            if type(x) is not int or not -(2**31) <= x < 2**31:
                raise ValueError(f"{v.name} requires a 32-bit integer")
        elif v.type == "Boolean" and type(x) is not bool:
            raise ValueError(f"{v.name} requires a boolean")
        elif v.type == "String" and (not isinstance(x, str) or "\0" in x):
            raise ValueError(f"{v.name} requires a string without NUL")
        if v.type in ("Real", "Integer", "Enumeration"):
            for attr, compare in (("min", operator.lt), ("max", operator.gt)):
                limit = getattr(v, attr, None)
                if limit is None:
                    limit = getattr(getattr(v, "declaredType", None), attr, None)
                if limit is not None and compare(x, float(limit)):
                    raise ValueError(f"{v.name} violates {attr}={limit}")

    def assignments(values, initial=False):
        if not isinstance(values, dict):
            raise ValueError("Assignments must be objects")
        for name, x in values.items():
            v = variable(name)
            allowed = v.causality == "input" or (
                initial and v.causality == "parameter" and v.variability in ("fixed", "tunable"))
            if not allowed:
                raise ValueError(f"Variable is not writable here: {name}")
            value(v, x)

    assignments(scenario.get("initial", {}), initial=True)
    for key in ("events", "rules", "checks", "outputs"):
        if not isinstance(scenario.get(key, []), list):
            raise ValueError(f"{key} must be an array")
    outputs = scenario["outputs"]
    if not outputs or any(not isinstance(n, str) for n in outputs) or len(set(outputs)) != len(outputs):
        raise ValueError("outputs must be a nonempty list of unique variable names")
    if "time" in outputs:
        raise ValueError("'time' is reserved for the result timestamp")
    for name in outputs:
        variable(name)
    occupied = set()
    for event in scenario.get("events", []):
        fields(event, ("time", "set"), ("time", "set"))
        index = tick(event["time"])
        if not 0 <= index < count:
            raise ValueError("Events must occur before stop_time and at/after start_time")
        assignments(event["set"])
        for name in event["set"]:
            if (index, name) in occupied:
                raise ValueError(f"Conflicting events for {name}")
            occupied.add((index, name))
    for rule in scenario.get("rules", []):
        fields(rule, ("when", "set", "stop"), ("when",))
        if not rule.get("set") and rule.get("stop") is not True:
            raise ValueError("A rule needs set assignments or stop=true")
        if "stop" in rule and type(rule["stop"]) is not bool:
            raise ValueError("stop must be boolean")
        condition = rule["when"]
        fields(condition, ("variable", "op", "value"), ("variable", "op", "value"))
        v = variable(condition["variable"])
        if condition["op"] not in OPS:
            raise ValueError("Unknown comparison operator")
        if v.type in ("Boolean", "String") and condition["op"] not in ("==", "!="):
            raise ValueError("Boolean and String conditions support only == and !=")
        value(v, condition["value"])
        assignments(rule.get("set", {}))
    for check in scenario.get("checks", []):
        fields(check, ("variable", "min", "max"), ("variable",))
        v = variable(check["variable"])
        if v.type not in ("Real", "Integer", "Enumeration") or v.name not in outputs:
            raise ValueError("Checks require a numeric variable included in outputs")
        if "min" not in check and "max" not in check:
            raise ValueError("A check needs min or max")
        for key in ("min", "max"):
            if key in check:
                number(check[key])
        if check.get("min", -math.inf) > check.get("max", math.inf):
            raise ValueError("Check min exceeds max")
    return variables, count


def run_scenario(fmu_path, scenario, cancelled=None):
    """Execute a validated scenario and return JSON-serializable samples and checks."""
    md = read_model_description(str(fmu_path))
    variables, count = validate_scenario(scenario, md)
    start, step = scenario.get("start_time", 0.0), scenario["step_size"]
    events = {}
    for event in scenario.get("events", []):
        index = round((event["time"] - start) / step)
        events.setdefault(index, {}).update(event["set"])
    rows, fired = [], set()
    reason = "stop_time"
    with TemporaryDirectory(prefix="fmpy-scenario-") as directory:
        extract(str(fmu_path), unzipdir=directory)
        fmu = FMU2Slave(guid=md.guid, unzipDirectory=directory,
                        modelIdentifier=md.coSimulation.modelIdentifier,
                        instanceName="scenario")
        instantiated = initialized = False

        def read(name):
            v = variables[name]
            kind = "Integer" if v.type == "Enumeration" else v.type
            result = getattr(fmu, "get" + kind)([v.valueReference])[0]
            return result.decode("utf-8") if isinstance(result, bytes) else result

        def write(values):
            for name, x in values.items():
                v = variables[name]
                kind = "Integer" if v.type == "Enumeration" else v.type
                getattr(fmu, "set" + kind)([v.valueReference], [x])

        try:
            fmu.instantiate()
            instantiated = True
            fmu.setupExperiment(startTime=start, stopTime=scenario["stop_time"])
            write(scenario.get("initial", {}))
            write(events.get(0, {}))
            fmu.enterInitializationMode()
            fmu.exitInitializationMode()
            initialized = True
            for index in range(count + 1):
                if cancelled is not None and cancelled():
                    raise RuntimeError("Scenario cancelled")
                time = start + index * step
                rows.append({"time": time, **{n: read(n) for n in scenario["outputs"]}})
                if index == count:
                    break
                if index:
                    write(events.get(index, {}))
                # Evaluate all rules on one snapshot; apply once in listed order.
                triggered = []
                for i, rule in enumerate(scenario.get("rules", [])):
                    c = rule["when"]
                    if i not in fired and OPS[c["op"]](read(c["variable"]), c["value"]):
                        triggered.append((i, rule))
                for i, rule in triggered:
                    fired.add(i)
                    write(rule.get("set", {}))
                if any(rule.get("stop", False) for _, rule in triggered):
                    reason = "rule"
                    break
                fmu.doStep(currentCommunicationPoint=time, communicationStepSize=step)
        finally:
            try:
                if initialized:
                    fmu.terminate()
            finally:
                if instantiated:
                    fmu.freeInstance()
    checks = []
    for check in scenario.get("checks", []):
        failed = [r["time"] for r in rows if not (
            math.isfinite(r[check["variable"]]) and
            check.get("min", -math.inf) <= r[check["variable"]] <= check.get("max", math.inf))]
        checks.append({**check, "passed": not failed, "first_failure_time": failed[0] if failed else None})
    return {"samples": rows, "checks": checks, "passed": all(c["passed"] for c in checks) if checks else None,
            "termination": reason, "fired_rules": sorted(fired)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fmu", type=Path)
    parser.add_argument("scenario", type=Path, nargs="?")
    parser.add_argument("--inspect", action="store_true", help="Print FMU interface as JSON")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("scenario-results"))
    args = parser.parse_args()
    try:
        md = read_model_description(str(args.fmu))
        if args.inspect:
            print(json.dumps({"fmi_version": md.fmiVersion, "co_simulation": md.coSimulation is not None,
                              "variables": [{k: getattr(v, k, None) for k in
                                             ("name", "type", "causality", "variability", "unit", "description", "start")}
                                            for v in md.modelVariables]}, ensure_ascii=False, indent=2))
            return 0
        if args.scenario is None:
            parser.error("scenario is required unless --inspect is used")
        scenario = json.loads(args.scenario.read_text(encoding="utf-8-sig"))
        validate_scenario(scenario, md)
        if args.validate_only:
            print("Scenario is valid")
            return 0
        result = run_scenario(args.fmu, scenario)
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output / "results.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["time", *scenario["outputs"]])
            writer.writeheader()
            writer.writerows(result.pop("samples"))
        (args.output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 1 if result["passed"] is False else 0
    except (ValueError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
