"""Deterministic JSON scenarios for FMI 2.0 Co-Simulation.

Run with ``python -m fmpy.scenario --help``. No AI runtime is required.
Validation reports every problem it finds with a machine-readable code and a
JSON path, so a caller (a GUI, a script, an agent) can repair a scenario in one
round trip. See ``fmpy.scenario_agent`` for the JSON-in/JSON-out tool surface.
"""

import argparse
import csv
import difflib
import json
import math
import operator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fmpy import extract, read_model_description
from fmpy.fmi1 import FMICallException
from fmpy.fmi2 import FMU2Slave, fmi2Discard, fmi2LastSuccessfulTime, fmi2Terminated


OPS = {"==": operator.eq, "!=": operator.ne, ">": operator.gt,
       ">=": operator.ge, "<": operator.lt, "<=": operator.le}
TYPES = {"Real", "Integer", "Enumeration", "Boolean", "String"}
NUMERIC = ("Real", "Integer", "Enumeration")
# FMI kind used for the get/set call of each declared variable type.
KIND = {"Real": "Real", "Integer": "Integer", "Enumeration": "Integer",
        "Boolean": "Boolean", "String": "String"}
MAX_STEPS = 1_000_000
MAX_LOG_ENTRIES = 200
MAX_LISTED_CHANGES = 20
STATUS_LABELS = ("OK", "WARNING", "DISCARD", "ERROR", "FATAL", "PENDING")


def join_path(prefix, path):
    """Join two JSON path fragments: ``("events[0]", "set.u")`` -> ``events[0].set.u``."""
    if not prefix or not path:
        return prefix or path
    return prefix + ("" if path.startswith("[") else ".") + path


class ScenarioError(ValueError):
    """A single validation problem located by a JSON path."""

    def __init__(self, code, message, path="", **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.extra = extra

    def at(self, prefix):
        """Return the same problem with ``prefix`` prepended to its path."""
        return ScenarioError(self.code, self.message, join_path(prefix, self.path), **self.extra)

    def as_dict(self):
        return {"code": self.code, "path": self.path, "message": self.message, **self.extra}


class ScenarioValidationError(ValueError):
    """Every problem found while validating one scenario."""

    def __init__(self, errors):
        self.errors = list(errors)
        head = self.errors[0]
        text = f"{head.path}: {head.message}" if head.path else head.message
        if len(self.errors) > 1:
            text += f" (and {len(self.errors) - 1} more problem(s))"
        super().__init__(text)

    def as_dict(self):
        return {"errors": [error.as_dict() for error in self.errors]}


class ScenarioCancelled(RuntimeError):
    """Raised when a caller's ``cancelled`` callback asks the run to stop."""


class LogCapture:
    """Keep a bounded copy of the messages the FMU reports through its logger.

    An FMU's own warnings and errors are the only account of why it refused a
    call. The command line used to print them and the GUI dropped them; keeping
    them in the report means whoever reads the result can see them.
    """

    def __init__(self, limit=MAX_LOG_ENTRIES):
        self.limit = limit
        self.entries = []
        self.dropped = 0
        self.time = None

    def __call__(self, componentEnvironment, instanceName, status, category, message):
        if len(self.entries) >= self.limit:
            self.dropped += 1
            return
        label = STATUS_LABELS[status] if 0 <= status < len(STATUS_LABELS) else str(status)
        self.entries.append({"time": self.time, "status": label,
                             "category": (category or b"").decode("utf-8", "replace"),
                             "message": (message or b"").decode("utf-8", "replace")})

    def count(self, *labels):
        return sum(1 for entry in self.entries if entry["status"] in labels)


class Problems:
    """Collect problems so that one call reports every independent mistake."""

    def __init__(self):
        self.errors = []
        self.prefix = ""

    def add(self, code, message, path="", **extra):
        self.errors.append(ScenarioError(code, message, join_path(self.prefix, path), **extra))

    @contextmanager
    def at(self, prefix):
        """Record a problem raised inside the block and carry on with the next item.

        Scopes nest, so an error raised while checking one assignment is reported
        at its full path.
        """
        outer = self.prefix
        self.prefix = join_path(outer, prefix)
        try:
            yield
        except ScenarioError as error:
            self.errors.append(error.at(self.prefix))
        finally:
            self.prefix = outer

    def check(self):
        if self.errors:
            raise ScenarioValidationError(self.errors)


def fields(obj, allowed, required=(), labelled=False):
    """Reject unknown and missing fields. ``labelled`` allows id and description."""
    allowed = tuple(allowed) + (("id", "description") if labelled else ())
    if not isinstance(obj, dict):
        raise ScenarioError("not_an_object", f"Expected an object, got {type(obj).__name__}")
    for name in sorted(set(obj) - set(allowed)):
        raise ScenarioError("unknown_field", f"Unknown field: {name}", name, allowed=sorted(allowed))
    for name in required:
        if name not in obj:
            raise ScenarioError("missing_field", f"Required field is missing: {name}", name)
    for name in ("id", "description"):
        if name in obj and not isinstance(obj[name], str):
            raise ScenarioError("type_mismatch", f"{name} must be a string", name)
    return obj


def number(value, path=""):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ScenarioError("not_a_number", f"Expected a finite number, got {value!r}", path)
    return value


def validate_scenario(scenario, md):
    """Validate before instantiating native FMU code. Return variables and step count.

    Raises :class:`ScenarioValidationError`, which lists every problem found.
    """
    problems = Problems()
    try:
        fields(scenario, ("version", "name", "description", "metadata", "start_time", "stop_time",
                          "step_size", "initial", "events", "rules", "outputs", "checks"),
               ("version", "stop_time", "step_size", "outputs"))
    except ScenarioError as error:
        raise ScenarioValidationError([error])
    if type(scenario["version"]) is not int or scenario["version"] != 1:
        raise ScenarioValidationError([ScenarioError(
            "unsupported_version", "Scenario version must be 1", "version")])
    if md.fmiVersion != "2.0" or md.coSimulation is None:
        raise ScenarioValidationError([ScenarioError(
            "unsupported_fmu", "Only FMI 2.0 Co-Simulation is supported", "",
            fmi_version=md.fmiVersion, co_simulation=md.coSimulation is not None)])
    for name in ("name", "description"):
        if name in scenario and not isinstance(scenario[name], str):
            problems.add("type_mismatch", f"{name} must be a string", name)
    if "metadata" in scenario and not isinstance(scenario["metadata"], dict):
        problems.add("type_mismatch", "metadata must be an object", "metadata")

    variables = {v.name: v for v in md.modelVariables}
    try:
        start = number(scenario.get("start_time", 0.0), "start_time")
        stop = number(scenario["stop_time"], "stop_time")
        step = number(scenario["step_size"], "step_size")
        if step <= 0 or stop <= start:
            raise ScenarioError("invalid_time_range",
                                "Require step_size > 0 and stop_time > start_time")
    except ScenarioError as error:
        raise ScenarioValidationError(problems.errors + [error])

    def tick(time, path=""):
        ratio = (number(time, path) - start) / step
        if not math.isfinite(ratio) or not math.isclose(ratio, round(ratio), abs_tol=1e-8, rel_tol=0):
            raise ScenarioError("off_grid", "Times must lie on the fixed communication grid", path,
                                start_time=start, step_size=step)
        return round(ratio)

    try:
        count = tick(stop, "stop_time")
        if not 1 <= count <= MAX_STEPS:
            raise ScenarioError("too_many_steps",
                                f"Scenario must contain 1 to {MAX_STEPS:,} steps", "stop_time",
                                steps=count, maximum=MAX_STEPS)
    except ScenarioError as error:
        raise ScenarioValidationError(problems.errors + [error])

    def variable(name, path=""):
        if not isinstance(name, str):
            raise ScenarioError("type_mismatch", f"Variable name must be a string, got {name!r}", path)
        if name not in variables:
            raise ScenarioError("unknown_variable", f"Unknown variable: {name}", path,
                                candidates=difflib.get_close_matches(name, variables, n=5))
        v = variables[name]
        if v.type not in TYPES:
            raise ScenarioError("unsupported_type", f"Unsupported type for {name}: {v.type}", path,
                                variable_type=v.type, supported=sorted(TYPES))
        return v

    def value(v, x, path=""):
        if v.type == "Real":
            number(x, path)
        elif v.type in ("Integer", "Enumeration"):
            if type(x) is not int or not -(2 ** 31) <= x < 2 ** 31:
                raise ScenarioError("type_mismatch", f"{v.name} requires a 32-bit integer", path,
                                    variable_type=v.type)
        elif v.type == "Boolean" and type(x) is not bool:
            raise ScenarioError("type_mismatch", f"{v.name} requires a boolean", path,
                                variable_type=v.type)
        elif v.type == "String" and (not isinstance(x, str) or "\0" in x):
            raise ScenarioError("type_mismatch", f"{v.name} requires a string without NUL", path,
                                variable_type=v.type)
        if v.type in NUMERIC:
            for attr, compare in (("min", operator.lt), ("max", operator.gt)):
                limit = getattr(v, attr, None)
                if limit is None:
                    limit = getattr(getattr(v, "declaredType", None), attr, None)
                if limit is not None and compare(x, float(limit)):
                    raise ScenarioError("out_of_range", f"{v.name} violates {attr}={limit}", path,
                                        **{attr: float(limit)})

    def assignments(values, path, initial=False):
        """Validate every assignment, reporting each bad one at its own path."""
        if not isinstance(values, dict):
            raise ScenarioError("not_an_object", "Assignments must be objects", path)
        for name, x in values.items():
            with problems.at(join_path(path, name) if isinstance(name, str) else path):
                v = variable(name)
                allowed = v.causality == "input" or (
                    initial and v.causality == "parameter"
                    and v.variability in ("fixed", "tunable"))
                if not allowed:
                    raise ScenarioError("not_writable", f"Variable is not writable here: {name}",
                                        causality=v.causality, variability=v.variability)
                value(v, x)

    with problems.at(""):
        assignments(scenario.get("initial", {}), "initial", initial=True)
    malformed = False
    for key in ("events", "rules", "checks", "outputs"):
        if not isinstance(scenario.get(key, []), list):
            problems.add("type_mismatch", f"{key} must be an array", key)
            malformed = True
    if malformed:
        problems.check()

    outputs = scenario["outputs"]
    if not outputs:
        problems.add("empty_outputs", "outputs must be a nonempty list of unique variable names",
                     "outputs")
    seen = set()
    for i, name in enumerate(outputs):
        with problems.at(f"outputs[{i}]"):
            if name == "time":
                raise ScenarioError("reserved_name", "'time' is reserved for the result timestamp")
            if name in seen:
                raise ScenarioError("duplicate_output", f"Duplicate output: {name}")
            variable(name)
            seen.add(name)

    def identifier(obj, used):
        name = obj.get("id")
        if name is not None:
            if name in used:
                raise ScenarioError("duplicate_id", f"Duplicate id: {name}", "id")
            used.add(name)

    occupied, event_ids = set(), set()
    for i, event in enumerate(scenario.get("events", [])):
        with problems.at(f"events[{i}]"):
            fields(event, ("time", "set"), ("time", "set"), labelled=True)
            identifier(event, event_ids)
            index = tick(event["time"], "time")
            if not 0 <= index < count:
                raise ScenarioError("event_out_of_range",
                                    "Events must occur before stop_time and at/after start_time",
                                    "time")
            assignments(event["set"], "set")
            for name in event["set"]:
                if (index, name) in occupied:
                    raise ScenarioError("duplicate_event", f"Conflicting events for {name}",
                                        f"set.{name}", time=event["time"])
                occupied.add((index, name))

    rule_ids = set()
    for i, rule in enumerate(scenario.get("rules", [])):
        with problems.at(f"rules[{i}]"):
            fields(rule, ("when", "set", "stop"), ("when",), labelled=True)
            identifier(rule, rule_ids)
            if "stop" in rule and type(rule["stop"]) is not bool:
                raise ScenarioError("type_mismatch", "stop must be boolean", "stop")
            if not rule.get("set") and rule.get("stop") is not True:
                raise ScenarioError("no_action", "A rule needs set assignments or stop=true")
            condition = rule["when"]
            fields(condition, ("variable", "op", "value"), ("variable", "op", "value"))
            v = variable(condition["variable"], "when.variable")
            if condition["op"] not in OPS:
                raise ScenarioError("unknown_operator", "Unknown comparison operator", "when.op",
                                    allowed=sorted(OPS))
            if v.type in ("Boolean", "String") and condition["op"] not in ("==", "!="):
                raise ScenarioError("unsupported_operator",
                                    "Boolean and String conditions support only == and !=",
                                    "when.op", variable_type=v.type)
            value(v, condition["value"], "when.value")
            assignments(rule.get("set", {}), "set")

    check_ids = set()
    for i, check in enumerate(scenario.get("checks", [])):
        with problems.at(f"checks[{i}]"):
            fields(check, ("variable", "min", "max"), ("variable",), labelled=True)
            identifier(check, check_ids)
            v = variable(check["variable"], "variable")
            if v.type not in NUMERIC or v.name not in outputs:
                raise ScenarioError("invalid_check",
                                    "Checks require a numeric variable included in outputs",
                                    "variable", variable_type=v.type, in_outputs=v.name in outputs)
            if "min" not in check and "max" not in check:
                raise ScenarioError("invalid_check", "A check needs min or max")
            for key in ("min", "max"):
                if key in check:
                    number(check[key], key)
            if check.get("min", -math.inf) > check.get("max", math.inf):
                raise ScenarioError("invalid_check", "Check min exceeds max", "min")

    problems.check()
    return variables, count


def _batches(names, variables):
    """Group variables by FMI kind so each sample needs one call per kind."""
    groups = {}
    for name in names:
        kind = KIND[variables[name].type]
        group = groups.setdefault(kind, ([], []))
        group[0].append(name)
        group[1].append(variables[name].valueReference)
    return [(kind, tuple(names), tuple(refs)) for kind, (names, refs) in groups.items()]


def _summarize(rows, outputs, variables):
    """Condense the sample table into a few numbers per variable.

    A result table holds up to a million rows. This is what a caller that cannot
    read the whole table -- a report, a reviewer, an agent -- needs instead.
    """
    summary = {}
    for name in outputs:
        v = variables[name]
        series = [row[name] for row in rows]
        entry = {"type": v.type, "unit": getattr(v, "unit", None),
                 "first": series[0] if series else None,
                 "last": series[-1] if series else None}
        if not series:
            summary[name] = entry
            continue
        if v.type in NUMERIC:
            finite = [(x, row["time"]) for x, row in zip(series, rows) if math.isfinite(x)]
            entry["nonfinite_count"] = len(series) - len(finite)
            if finite:
                low, high = min(finite), max(finite)
                entry.update(min=low[0], min_time=low[1], max=high[0], max_time=high[1],
                             mean=math.fsum(x for x, _ in finite) / len(finite))
        else:
            changes = [{"time": row["time"], "value": x}
                       for i, (x, row) in enumerate(zip(series, rows))
                       if i == 0 or x != series[i - 1]]
            entry["change_count"] = len(changes) - 1
            entry["changes"] = changes[:MAX_LISTED_CHANGES]
            entry["changes_truncated"] = len(changes) > MAX_LISTED_CHANGES
            if v.type == "Boolean":
                entry["true_count"] = sum(1 for x in series if x)
                entry["false_count"] = len(series) - entry["true_count"]
            else:
                distinct = list(dict.fromkeys(series))
                entry["distinct_count"] = len(distinct)
                entry["distinct"] = distinct[:MAX_LISTED_CHANGES]
        summary[name] = entry
    return summary


def _evaluate_checks(rows, scenario):
    """Judge every check and report how much room was left, or lost."""
    results = []
    for check in scenario.get("checks", []):
        name = check["variable"]
        low, high = check.get("min", -math.inf), check.get("max", math.inf)
        failures, worst, worst_time, margin = 0, None, None, math.inf
        for row in rows:
            x = row[name]
            if not math.isfinite(x):
                failures += 1
                if margin > -math.inf:
                    worst, worst_time, margin = x, row["time"], -math.inf
                continue
            room = min(x - low, high - x)
            if room < margin:
                worst, worst_time, margin = x, row["time"], room
            if room < 0:
                failures += 1
        first_failure = next((row["time"] for row in rows if not (
            math.isfinite(row[name]) and low <= row[name] <= high)), None)
        results.append({**check, "passed": failures == 0, "first_failure_time": first_failure,
                        "failure_count": failures, "worst": worst, "worst_time": worst_time,
                        "margin": margin if math.isfinite(margin) else None})
    return results


def _callbacks(logger):
    """Build FMI 2.0 callbacks that forward the FMU's own log messages to ``logger``."""
    from ctypes import byref

    from fmpy import calloc, free
    from fmpy.fmi2 import (fmi2CallbackAllocateMemoryTYPE, fmi2CallbackFreeMemoryTYPE,
                           fmi2CallbackFunctions, fmi2CallbackLoggerTYPE)

    callbacks = fmi2CallbackFunctions()
    callbacks.logger = fmi2CallbackLoggerTYPE(logger)
    callbacks.allocateMemory = fmi2CallbackAllocateMemoryTYPE(calloc)
    callbacks.freeMemory = fmi2CallbackFreeMemoryTYPE(free)
    try:
        # The variadic message cannot reach Python directly; a native proxy formats it.
        from fmpy.logging import addLoggerProxy

        addLoggerProxy(byref(callbacks))
    except Exception:
        pass
    return callbacks


def run_scenario(fmu_path, scenario, cancelled=None, debug_logging=False):
    """Execute a validated scenario and return JSON-serializable samples and checks."""
    md = read_model_description(str(fmu_path))
    variables, count = validate_scenario(scenario, md)
    start, step = scenario.get("start_time", 0.0), scenario["step_size"]
    events = {}
    for event in scenario.get("events", []):
        index = round((event["time"] - start) / step)
        events.setdefault(index, {}).update(event["set"])
    rules = scenario.get("rules", [])
    rows, fired, rule_events = [], set(), []
    reason = "stop_time"
    log = LogCapture()
    log.time = start

    output_batches = _batches(scenario["outputs"], variables)
    watched = list(dict.fromkeys(rule["when"]["variable"] for rule in rules))
    watch_batches = _batches(watched, variables)

    with TemporaryDirectory(prefix="fmpy-scenario-") as directory:
        extract(str(fmu_path), unzipdir=directory)
        fmu = FMU2Slave(guid=md.guid, unzipDirectory=directory,
                        modelIdentifier=md.coSimulation.modelIdentifier,
                        instanceName="scenario")
        instantiated = initialized = False

        def sample(batches):
            values = {}
            for kind, names, refs in batches:
                for name, result in zip(names, getattr(fmu, "get" + kind)(list(refs))):
                    if isinstance(result, bytes):
                        result = result.decode("utf-8", "replace")
                    elif kind == "Boolean":
                        # fmi2Boolean is an int; report the JSON type the format accepts.
                        result = bool(result)
                    values[name] = result
            return values

        def write(values):
            for kind, names, refs in _batches(values, variables):
                getattr(fmu, "set" + kind)(list(refs), [values[name] for name in names])

        try:
            fmu.instantiate(callbacks=_callbacks(log), loggingOn=debug_logging)
            instantiated = True
            fmu.setupExperiment(startTime=start, stopTime=scenario["stop_time"])
            write(scenario.get("initial", {}))
            write(events.get(0, {}))
            fmu.enterInitializationMode()
            fmu.exitInitializationMode()
            initialized = True
            for index in range(count + 1):
                if cancelled is not None and cancelled():
                    raise ScenarioCancelled("Scenario cancelled")
                log.time = time = start + index * step
                rows.append({"time": time, **sample(output_batches)})
                if index == count:
                    break
                if index:
                    write(events.get(index, {}))
                # Evaluate all rules on one snapshot; apply once in listed order.
                snapshot = sample(watch_batches)
                triggered = []
                for i, rule in enumerate(rules):
                    c = rule["when"]
                    if i not in fired and OPS[c["op"]](snapshot[c["variable"]], c["value"]):
                        triggered.append((i, rule))
                for i, rule in triggered:
                    fired.add(i)
                    rule_events.append({
                        "index": i, "id": rule.get("id"), "time": time,
                        "trigger": {**rule["when"], "actual": snapshot[rule["when"]["variable"]]},
                        "set": rule.get("set", {}), "stop": bool(rule.get("stop", False))})
                    write(rule.get("set", {}))
                if any(rule.get("stop", False) for _, rule in triggered):
                    reason = "rule"
                    break
                try:
                    fmu.doStep(currentCommunicationPoint=time, communicationStepSize=step)
                except FMICallException as error:
                    # An FMU may refuse a step because it wants to stop. That is an
                    # outcome of the test, not a failure of the run.
                    if error.status != fmi2Discard or not fmu.getBooleanStatus(fmi2Terminated):
                        raise
                    reason = "fmu_terminated"
                    log.time = last_time = fmu.getRealStatus(fmi2LastSuccessfulTime)
                    if last_time > time:
                        rows.append({"time": last_time, **sample(output_batches)})
                    break
        finally:
            try:
                if initialized:
                    fmu.terminate()
            finally:
                if instantiated:
                    fmu.freeInstance()
                else:
                    # Otherwise the loaded library keeps the temporary directory
                    # locked on Windows and hides the error that got us here.
                    fmu.freeLibrary()

    checks = _evaluate_checks(rows, scenario)
    passed = all(c["passed"] for c in checks) if checks else None
    summary = {"start_time": start, "end_time": rows[-1]["time"] if rows else start,
               "step_size": step, "sample_count": len(rows), "planned_steps": count,
               "termination": reason, "passed": passed,
               "failed_checks": [c["variable"] for c in checks if not c["passed"]],
               "log_errors": log.count("ERROR", "FATAL"),
               "log_warnings": log.count("WARNING", "DISCARD"),
               "outputs": _summarize(rows, scenario["outputs"], variables)}
    return {"samples": rows, "checks": checks, "passed": passed, "termination": reason,
            "fired_rules": sorted(fired), "rule_events": rule_events, "summary": summary,
            "fmu_log": log.entries, "fmu_log_dropped": log.dropped}


def write_results(directory, scenario, result):
    """Write results.csv, report.json and scenario.json. Return the report."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["time", *scenario["outputs"]])
        writer.writeheader()
        writer.writerows(result["samples"])
    report = {k: v for k, v in result.items() if k != "samples"}
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / "scenario.json").write_text(
        json.dumps(scenario, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def inspect_fmu(md):
    """Describe an FMU's interface as JSON."""
    return {"fmi_version": md.fmiVersion, "co_simulation": md.coSimulation is not None,
            "variables": [{k: getattr(v, k, None) for k in
                           ("name", "type", "causality", "variability", "unit", "description", "start")}
                          for v in md.modelVariables]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fmu", type=Path)
    parser.add_argument("scenario", type=Path, nargs="?")
    parser.add_argument("--inspect", action="store_true", help="Print FMU interface as JSON")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--debug-logging", action="store_true",
                        help="Ask the FMU for debug log messages")
    parser.add_argument("--output", type=Path, default=Path("scenario-results"))
    args = parser.parse_args()
    try:
        md = read_model_description(str(args.fmu))
        if args.inspect:
            print(json.dumps(inspect_fmu(md), ensure_ascii=False, indent=2))
            return 0
        if args.scenario is None:
            parser.error("scenario is required unless --inspect is used")
        scenario = json.loads(args.scenario.read_text(encoding="utf-8-sig"))
        validate_scenario(scenario, md)
        if args.validate_only:
            print("Scenario is valid")
            return 0
        result = run_scenario(args.fmu, scenario, debug_logging=args.debug_logging)
        report = write_results(args.output, scenario, result)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if result["passed"] is False else 0
    except ScenarioValidationError as error:
        parser.exit(2, json.dumps(error.as_dict(), ensure_ascii=False, indent=2) + "\n")
    except (ValueError, OSError, FMICallException) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
