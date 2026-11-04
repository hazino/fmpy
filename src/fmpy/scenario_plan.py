"""Read what an FMU declares about itself and propose scenarios worth running.

An FMU states more than a variable list. ``modelDescription.xml`` also declares
which inputs each output depends on, how many states and event indicators the
model has, what its declared types and units mean, and which experiment the
exporter had in mind. That is enough to derive test scenarios mechanically:
which input to excite, which outputs to record, on which communication grid.

It is not enough to decide whether a result is *correct*. An FMU never states
what it is supposed to do, and its declared min/max are validity bounds for the
solver, not acceptance criteria. So every proposal here carries stimulus and
coverage, leaves ``checks`` out, and names what a domain source still has to
supply. ``fmpy.scenario_agent.plan`` exposes this as a tool.
"""

import zipfile
from pathlib import Path

from fmpy.scenario import MAX_STEPS, NUMERIC, TYPES

#: Proposing one scenario per input stays useful only for so long.
MAX_INPUT_PROPOSALS = 12
#: Communication points a proposed scenario aims for.
DEFAULT_STEPS = 50


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _limit(v, attr):
    """A variable's declared bound, from the variable or from its declared type."""
    value = getattr(v, attr, None)
    if value is None:
        value = getattr(getattr(v, "declaredType", None), attr, None)
    return _float(value)


def _enumeration_values(v):
    declared = getattr(v, "declaredType", None)
    values = [int(item.value) for item in getattr(declared, "items", None) or []
              if _float(item.value) is not None]
    return sorted(set(values))


def levels(v):
    """Two distinct settable values for one input: where to start, where to step to.

    Returns ``None`` for a variable this module will not invent a value for.
    """
    if v.type == "Boolean":
        return False, True
    if v.type == "String":
        return None
    low, high = _limit(v, "min"), _limit(v, "max")
    start = _float(getattr(v, "start", None))
    if v.type == "Enumeration":
        values = _enumeration_values(v)
        if len(values) >= 2:
            return values[0], values[-1]
        if not values:
            return None
        return values[0], values[0]
    if low is not None and high is not None and high > low:
        pair = (low, high)
    elif low is not None:
        pair = (low, low + 1)
    elif high is not None:
        pair = (high - 1, high)
    else:
        base = start if start is not None else 0.0
        pair = (base, base + 1)
    if v.type in ("Integer", "Enumeration"):
        pair = (int(pair[0]), int(pair[1]))
    else:
        pair = (float(pair[0]), float(pair[1]))
    return pair


def archive_contents(path):
    """List the parts of the FMU beyond the model description."""
    path = Path(path)
    try:
        if path.is_dir():
            names = [str(p.relative_to(path)).replace("\\", "/")
                     for p in path.rglob("*") if p.is_file()]
        else:
            with zipfile.ZipFile(path) as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return {"source_files": [], "documentation": [], "resources": [], "readable": False}
    return {"source_files": sorted(n for n in names if n.startswith("sources/")),
            "documentation": sorted(n for n in names if n.startswith("documentation/")),
            "resources": sorted(n for n in names if n.startswith("resources/")),
            "readable": True}


def model_structure(md, path=None):
    """Report the execution structure the FMU declares, as JSON."""
    usable = [v for v in md.modelVariables
              if v.type in TYPES and v.causality != "independent" and v.name != "time"]
    inputs = [v.name for v in usable if v.causality == "input"]
    dependencies, affects = {}, {name: [] for name in inputs}
    for unknown in md.outputs:
        if unknown.variable is None:
            continue
        name = unknown.variable.name
        declared = unknown.dependencies
        entry = {"depends_on": None if declared is None else [v.name for v in declared],
                 "kind": unknown.dependenciesKind}
        entry["inputs"] = (inputs if declared is None else
                           [v.name for v in declared if v.causality == "input"])
        dependencies[name] = entry
        for driver in entry["inputs"]:
            affects.setdefault(driver, []).append(name)

    cs = md.coSimulation
    return {
        "variable_naming_convention": md.variableNamingConvention,
        "declared_outputs": list(dependencies),
        "dependencies": dependencies,
        "dependency_note": "depends_on null means the FMU omitted the attribute, which per FMI "
                           "2.0 means the output may depend on every known variable. An empty "
                           "list means it depends on no input directly, so the value comes from "
                           "the model's own state.",
        "inputs_to_outputs": affects,
        "inputs_without_declared_effect": sorted(n for n, outs in affects.items() if not outs),
        "continuous_states": md.numberOfContinuousStates,
        "event_indicators": md.numberOfEventIndicators,
        "derivatives": len(md.derivatives),
        "initial_unknowns": len(md.initialUnknowns),
        "log_categories": [{"name": c.name, "description": c.description}
                           for c in md.logCategories],
        "co_simulation": {
            "model_identifier": getattr(cs, "modelIdentifier", None),
            "can_handle_variable_communication_step_size":
                getattr(cs, "canHandleVariableCommunicationStepSize", None),
            "can_get_and_set_fmu_state": getattr(cs, "canGetAndSetFMUstate", None),
            "max_output_derivative_order": getattr(cs, "maxOutputDerivativeOrder", None),
            "needs_execution_tool": getattr(cs, "needsExecutionTool", None),
        } if cs is not None else None,
        "type_definitions": [
            {"name": t.name, "type": t.type, "quantity": t.quantity, "unit": t.unit,
             "min": _float(t.min), "max": _float(t.max), "description": t.description,
             "items": [{"name": i.name, "value": _float(i.value), "description": i.description}
                       for i in t.items]}
            for t in md.typeDefinitions],
        "units": [u.name for u in md.unitDefinitions],
        "subsystems": sorted({v.name.split(".")[0] for v in usable if "." in v.name}),
        "archive": archive_contents(path) if path is not None else None,
    }


#: Each check reports a fact about the model description, what it costs when
#: deriving scenarios, and the declaration that would remove the cost. Whoever
#: owns the exporter -- a hand-written modelDescription.xml, a CMakeLists that
#: builds the FMU -- can act on every one of these.
def review_declaration(md, path=None):
    """Report which declarations are missing and what each one costs.

    ``plan`` can only use what the FMU declares. This says where the declaration
    is thin, so the exporter can be improved instead of the test being guessed.
    """
    usable = [v for v in md.modelVariables
              if v.type in TYPES and v.causality != "independent" and v.name != "time"]
    inputs = [v for v in usable if v.causality == "input"]
    settable = [v for v in usable if v.causality == "input" or (
        v.causality == "parameter" and v.variability in ("fixed", "tunable"))]
    structure = model_structure(md, path)
    findings = []

    def finding(code, severity, affects, effect, fix):
        if affects:
            findings.append({"code": code, "severity": severity,
                             "count": len(affects), "affects": sorted(affects)[:20],
                             "effect": effect, "fix": fix})

    supported = md.fmiVersion == "2.0" and md.coSimulation is not None
    if not supported:
        findings.append({
            "code": "unsupported_interface", "severity": "blocking", "count": 1,
            "affects": [md.modelName], "effect": "the scenario runner cannot execute this FMU",
            "fix": "export FMI 2.0 with a CoSimulation interface, or extend the runner"})
    unsupported = [v.name for v in md.modelVariables if v.type not in TYPES]
    finding("unsupported_variable_types", "blocking", unsupported,
            "these variables cannot be read or written by a scenario",
            "FMI 2.0 declares Real, Integer, Enumeration, Boolean and String")

    if not md.outputs:
        findings.append({
            "code": "no_declared_outputs", "severity": "high", "count": 1,
            "affects": [md.modelName],
            "effect": "there is no statement of what the model is for, so proposals record "
                      "locals and parameters as well",
            "fix": "list the outputs in ModelStructure/Outputs"})
    finding("undeclared_dependencies", "high",
            [name for name, edge in structure["dependencies"].items()
             if edge["depends_on"] is None],
            "an output with no declared dependencies may depend on anything, so a step "
            "proposal has to record every output instead of the ones it drives",
            "add the dependencies attribute to ModelStructure/Outputs/Unknown")
    finding("inputs_without_declared_effect", "medium",
            structure["inputs_without_declared_effect"],
            "no output declares a dependency on these inputs, so nothing says what to watch "
            "when they move",
            "declare the dependency, or confirm the input really has no effect")
    finding("missing_range", "medium",
            [v.name for v in settable if v.type in NUMERIC
             and _limit(v, "min") is None and _limit(v, "max") is None],
            "stimulus levels have to be invented from the start value, so a proposal may "
            "drive the model outside its operating range",
            "declare min and max on the variable or on its declared type")
    finding("missing_description", "medium", [v.name for v in usable if not v.description],
            "a generator has only the variable name to go on",
            "add a description attribute to each variable")
    finding("missing_unit", "medium",
            [v.name for v in usable if v.type == "Real"
             and not getattr(v, "unit", None)
             and not getattr(getattr(v, "declaredType", None), "unit", None)],
            "a value cannot be checked against a physical limit without knowing its unit",
            "declare a unit, or a declared type that carries one")
    finding("enumeration_without_items", "medium",
            [v.name for v in usable if v.type == "Enumeration"
             and not _enumeration_values(v)],
            "the legal values of the enumeration are unknown, so it cannot be exercised",
            "declare the Enumeration items in TypeDefinitions")
    finding("string_input_without_start", "low",
            [v.name for v in inputs if v.type == "String" and not isinstance(
                getattr(v, "start", None), str)],
            "no string value can be invented, so the input is left untouched",
            "declare a start value, or supply the strings with the test")

    experiment = md.defaultExperiment
    missing_grid = [key for key in ("stopTime", "stepSize")
                    if _float(getattr(experiment, key, None)) is None
                    or _float(getattr(experiment, key, None)) <= 0]
    finding("missing_default_experiment", "medium", missing_grid,
            "the communication grid has to be guessed; the span is split into "
            f"{DEFAULT_STEPS} steps",
            "declare DefaultExperiment stopTime and stepSize")
    finding("no_log_categories", "low", [] if md.logCategories else [md.modelName],
            "debug logging has no category to enable, so the FMU may stay silent about why "
            "it refused a call",
            "declare LogCategories and emit messages through the logger callback")

    notes = []
    if md.numberOfEventIndicators:
        notes.append(f"{md.numberOfEventIndicators} event indicator(s) are declared. A fixed "
                     "communication grid locates those events only to within one step; the "
                     "declaration cannot say which step size is fine enough.")
    if structure["variable_naming_convention"] == "flat" and len(usable) > 20:
        notes.append(f"{len(usable)} variables with flat naming. A structured naming convention "
                     "would group them into subsystems a reader can navigate.")
    notes.append("No declaration can supply acceptance criteria. Pass/fail always comes from a "
                 "requirement, never from the FMU.")

    order = {"blocking": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order[f["severity"]], f["code"]))
    return {"model_name": md.modelName, "fmi_version": md.fmiVersion,
            "runnable_by_scenario_runner": supported,
            "variable_count": len(usable), "declared_output_count": len(md.outputs),
            "findings": findings,
            "blocking": [f["code"] for f in findings if f["severity"] == "blocking"],
            "notes": notes}


def grid(md, minimum_steps=10, steps=DEFAULT_STEPS):
    """Pick a communication grid, preferring what the FMU proposes for itself."""
    experiment = md.defaultExperiment
    start = _float(getattr(experiment, "startTime", None)) or 0.0
    stop = _float(getattr(experiment, "stopTime", None))
    stop = stop if stop is not None and stop > start else start + 1.0
    declared = _float(getattr(experiment, "stepSize", None))
    span = stop - start
    if declared is not None and declared > 0 and minimum_steps <= span / declared <= MAX_STEPS:
        step, count = declared, round(span / declared)
    else:
        count = max(minimum_steps, steps)
        step = round(span / count, 9) or span / count
    return start, round(step, 9), max(minimum_steps, count)


def _at(start, step, index):
    return round(start + index * step, 9)


def propose_scenarios(md, path=None):
    """Derive scenarios that are valid for this FMU and worth running as tests."""
    structure = model_structure(md, path)
    usable = [v for v in md.modelVariables
              if v.type in TYPES and v.causality != "independent" and v.name != "time"]
    by_name = {v.name: v for v in usable}
    inputs = [v for v in usable if v.causality == "input"]
    parameters = [v for v in usable
                  if v.causality == "parameter" and v.variability in ("fixed", "tunable")]
    recorded = structure["declared_outputs"] or [
        v.name for v in usable if v.causality in ("output", "local", "calculatedParameter")]
    recorded = [name for name in recorded if name in by_name] or [v.name for v in usable]

    baseline, steppable = {}, []
    for v in inputs:
        pair = levels(v)
        if pair is None:
            start = getattr(v, "start", None)
            if isinstance(start, str):
                baseline[v.name] = start
            continue
        baseline[v.name] = pair[0]
        if pair[0] != pair[1]:
            steppable.append((v, pair))

    start, step, count = grid(md)
    stop = _at(start, step, count)
    proposals = []

    def scenario(name, description, **extra):
        body = {"version": 1, "name": name, "description": description,
                "start_time": start, "stop_time": stop, "step_size": step}
        body.update(extra)
        body["outputs"] = extra.get("outputs", list(recorded))
        return body

    proposals.append({
        "id": "smoke",
        "title": "Runs on this grid at all",
        "rationale": "Every input is driven to a known value and every declared output is "
                     "recorded, so a failure here is the FMU refusing the grid or the start "
                     "values, not a disagreement about behaviour.",
        "covers": {"inputs": sorted(baseline), "outputs": list(recorded)},
        "needs_from_domain": ["the acceptance range for each recorded output"],
        "scenario": scenario("smoke test",
                             "drive every input to a known value and record every output",
                             initial=dict(baseline)),
    })

    truncated = steppable[MAX_INPUT_PROPOSALS:]
    for v, (low, high) in steppable[:MAX_INPUT_PROPOSALS]:
        observed = structure["inputs_to_outputs"].get(v.name) or list(recorded)
        outputs = list(dict.fromkeys([v.name, *observed]))
        proposals.append({
            "id": f"step-{v.name}",
            "title": f"Step {v.name} and watch what it drives",
            "rationale": ("The FMU declares that " + ", ".join(observed) + " depend on "
                          f"{v.name}." if structure["inputs_to_outputs"].get(v.name) else
                          f"The FMU declares no dependency for {v.name}, so every output is "
                          "recorded instead."),
            "covers": {"inputs": [v.name], "outputs": outputs},
            "needs_from_domain": [
                f"the expected response of {', '.join(observed)} to {v.name}",
                f"the operating range of {v.name}; {low} and {high} come from its declared "
                "bounds or start value" if v.type in NUMERIC else
                f"whether moving {v.name} from {low} to {high} is a case worth testing"],
            "scenario": scenario(
                f"step {v.name}",
                f"hold {v.name} at {low}, step to {high} after a fifth of the run",
                initial={**baseline, v.name: low},
                events=[{"id": "step", "time": _at(start, step, max(1, count // 5)),
                         "set": {v.name: high}}],
                outputs=outputs),
        })

    if len(steppable) > 1:
        spacing = max(1, count // (len(steppable) + 1))
        if spacing * len(steppable) < count:
            proposals.append({
                "id": "dependency-coverage",
                "title": "Exercise every declared input in one run",
                "rationale": "Each input is stepped at its own communication point, so one run "
                             "touches every declared input-to-output edge. Use it to find inputs "
                             "that change nothing, or that change something they should not.",
                "covers": {"inputs": [v.name for v, _ in steppable], "outputs": list(recorded)},
                "needs_from_domain": ["which of these inputs are allowed to interact"],
                "scenario": scenario(
                    "input coverage sweep",
                    "step each input in turn and record every declared output",
                    initial=dict(baseline),
                    events=[{"id": f"step-{v.name}", "time": _at(start, step, (i + 1) * spacing),
                             "set": {v.name: high}}
                            for i, (v, (_, high)) in enumerate(steppable)],
                    outputs=list(dict.fromkeys([*(v.name for v, _ in steppable), *recorded]))),
            })

    bounded = {}
    for v in parameters:
        pair = levels(v)
        if pair is not None and (_limit(v, "min") is not None or _limit(v, "max") is not None):
            bounded[v.name] = pair[1]
    if bounded:
        proposals.append({
            "id": "parameters-at-declared-bounds",
            "title": "Run with every bounded parameter at its declared limit",
            "rationale": "Parameters can only be set before initialization, so their limits are "
                         "never covered by a run that only moves inputs.",
            "covers": {"inputs": sorted(baseline), "outputs": list(recorded),
                       "parameters": sorted(bounded)},
            "needs_from_domain": ["whether these parameter limits are reachable in service"],
            "scenario": scenario("parameters at declared bounds",
                                 "set every bounded parameter to its declared limit",
                                 initial={**baseline, **bounded}),
        })

    notes = [
        "These proposals are stimulus and coverage. They carry no checks, because the FMU does "
        "not state what counts as a pass; add checks from a requirement or a domain document.",
        "Declared min/max are validity bounds for the model, not acceptance limits.",
        f"The grid is {step} s over {count} steps, taken from the FMU's default experiment where "
        "it declares one. A model with event indicators or fast dynamics may need a finer grid; "
        "the FMU does not say which.",
        "Dependencies come from ModelStructure. An exporter may declare them conservatively, so "
        "an absent edge is weaker evidence than a present one.",
    ]
    if md.numberOfEventIndicators:
        notes.append(f"The model declares {md.numberOfEventIndicators} event indicator(s). "
                     "A fixed communication grid locates those events only to within one step.")
    if structure["inputs_without_declared_effect"]:
        notes.append("No output declares a dependency on: "
                     + ", ".join(structure["inputs_without_declared_effect"]) + ".")
    if truncated:
        notes.append(f"{len(truncated)} further input(s) were not given their own proposal: "
                     + ", ".join(v.name for v, _ in truncated) + ".")
    strings = [v.name for v in inputs if v.type == "String"]
    if strings:
        notes.append("String inputs are held at their start value; no value is invented for "
                     + ", ".join(strings) + ".")
    sources = (structure["archive"] or {}).get("source_files") or []
    if sources:
        notes.append(f"The FMU also ships {len(sources)} source file(s) under sources/. They "
                     "describe the implementation, not the requirement, and for a generated FMU "
                     "they are usually machine-written; prefer the declared structure above.")

    for proposal in proposals:
        # Report what the scenario actually records, not what was asked for.
        proposal["covers"]["outputs"] = list(proposal["scenario"]["outputs"])

    return {"structure": structure,
            "grid": {"start_time": start, "stop_time": stop, "step_size": step, "steps": count},
            "proposals": proposals, "notes": notes}
