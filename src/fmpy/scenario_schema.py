"""Machine-readable contract for the FMI 2.0 scenario format.

The JSON Schema states what every scenario must look like. It cannot state
which variable names or value types a particular FMU accepts, so a generator
needs the interface description too -- see
``fmpy.scenario_agent.authoring_context``.
"""

from copy import deepcopy

from fmpy.scenario import MAX_STEPS

SCHEMA_ID = "https://github.com/hazino/fmpy/scenario/v1"

#: What this runner can and cannot do. A generator should read this before
#: emitting a scenario so it never asks for a feature that does not exist.
CAPABILITIES = {
    "format_version": 1,
    "fmi_versions": ["2.0"],
    "interface_types": ["CoSimulation"],
    "supported_variable_types": ["Real", "Integer", "Enumeration", "Boolean", "String"],
    "supported": [
        "fixed communication step size",
        "initial values for inputs and fixed/tunable parameters",
        "time events that hold an input value until the next change",
        "single-comparison rules that set inputs or stop the run, at most once each",
        "numeric min/max range checks over the whole recorded interval",
        "per-variable result summary, rule provenance and captured FMU log messages",
    ],
    "not_supported": [
        "ramps or linear interpolation between event times",
        "condition hold time, delay after a condition, repeated rules",
        "compound conditions (and/or)",
        "variable step size, rollback, asynchronous stepping",
        "coupling several FMUs",
        "unit conversion",
    ],
    "limits": {"max_steps": MAX_STEPS, "min_steps": 1},
    "semantics": [
        "Times are seconds. stop_time and every event time must be an integer "
        "multiple of step_size measured from start_time.",
        "At each communication point the runner records the outputs, then applies "
        "the time events, then evaluates the rules, then calls doStep. An input "
        "changed at time t therefore shows up in the sample at t + step_size.",
        "Rules are evaluated on one snapshot and applied in array order; each rule "
        "fires at most once for the whole run.",
        "At stop_time the runner only records; it applies no events and no rules.",
        "stop=true skips the next doStep, so the response to inputs set by the same "
        "rule is never computed.",
        "checks cover the recorded interval including the first sample. If a rule "
        "ends the run early, only the collected interval is judged.",
        "With no checks the verdict is null: a successful run is not a passed test.",
    ],
}

_LABEL = {
    "id": {"type": "string", "description": "Optional stable name used in reports."},
    "description": {"type": "string", "description": "Optional human-readable intent."},
}

_ASSIGNMENT = {
    "type": "object",
    "description": "Variable name to value. Values must match the FMU's declared type: "
                   "a number for Real, an integer for Integer/Enumeration, true/false "
                   "for Boolean, a string for String.",
    "minProperties": 1,
    "additionalProperties": {"type": ["number", "integer", "boolean", "string"]},
}

SCENARIO_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_ID,
    "title": "FMI 2.0 Co-Simulation scenario",
    "description": "A deterministic test case: stimulus, reactions and pass/fail criteria. "
                   "Variable names and value types are checked against the FMU, not here.",
    "type": "object",
    "required": ["version", "stop_time", "step_size", "outputs"],
    "additionalProperties": False,
    "properties": {
        "version": {"const": 1},
        "name": {"type": "string", "description": "Optional name of the test case."},
        "description": {"type": "string", "description": "Optional purpose of the test case."},
        "metadata": {
            "type": "object",
            "description": "Free-form provenance, copied into the report untouched. "
                           "A generator can record its model, prompt or trace id here.",
        },
        "start_time": {"type": "number", "default": 0.0},
        "stop_time": {"type": "number", "exclusiveMinimum": 0,
                      "description": "Must be start_time plus an integer multiple of step_size."},
        "step_size": {"type": "number", "exclusiveMinimum": 0,
                      "description": "Fixed communication interval, not the FMU's internal "
                                     "solver step."},
        "initial": dict(_ASSIGNMENT, description=(
            "Values applied before initialization. Inputs and fixed/tunable parameters only."),
            minProperties=0),
        "events": {
            "type": "array",
            "description": "Time-scheduled input changes. A value holds until the next change.",
            "items": {
                "type": "object",
                "required": ["time", "set"],
                "additionalProperties": False,
                "properties": {
                    **_LABEL,
                    "time": {"type": "number",
                             "description": "On the communication grid, before stop_time."},
                    "set": dict(_ASSIGNMENT, description="Inputs to change. Inputs only."),
                },
            },
        },
        "rules": {
            "type": "array",
            "description": "Reactions evaluated at every communication point. "
                           "Each rule fires at most once.",
            "items": {
                "type": "object",
                "required": ["when"],
                "additionalProperties": False,
                "properties": {
                    **_LABEL,
                    "when": {
                        "type": "object",
                        "required": ["variable", "op", "value"],
                        "additionalProperties": False,
                        "properties": {
                            "variable": {"type": "string",
                                         "description": "Any readable variable of the FMU."},
                            "op": {"enum": ["==", "!=", ">", ">=", "<", "<="],
                                   "description": "Boolean and String allow only == and !=."},
                            "value": {"type": ["number", "integer", "boolean", "string"]},
                        },
                    },
                    "set": dict(_ASSIGNMENT, description="Inputs to change when the rule fires."),
                    "stop": {"type": "boolean",
                             "description": "End the run without a further doStep."},
                },
                "anyOf": [{"required": ["set"]}, {"properties": {"stop": {"const": True}},
                                                  "required": ["stop"]}],
            },
        },
        "outputs": {
            "type": "array",
            "description": "Variables recorded at every communication point. "
                           "'time' is added automatically and must not be listed.",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "checks": {
            "type": "array",
            "description": "Pass/fail criteria. Without them the verdict is null.",
            "items": {
                "type": "object",
                "required": ["variable"],
                "additionalProperties": False,
                "anyOf": [{"required": ["min"]}, {"required": ["max"]}],
                "properties": {
                    **_LABEL,
                    "variable": {"type": "string",
                                 "description": "A numeric variable that is also in outputs."},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                },
            },
        },
    },
}

#: Every code ``validate_scenario`` can report, with the repair it calls for.
ERROR_CODES = {
    "not_an_object": "The scenario or one of its objects is not a JSON object.",
    "unknown_field": "Remove the field; see 'allowed' for the accepted names.",
    "missing_field": "Add the required field.",
    "type_mismatch": "Use the JSON type the variable or field requires.",
    "unsupported_version": "Set version to 1.",
    "unsupported_fmu": "The FMU is not FMI 2.0 Co-Simulation; nothing in the scenario can fix it.",
    "not_a_number": "Replace NaN, Infinity, a boolean or a string with a finite number.",
    "invalid_time_range": "Require step_size > 0 and stop_time > start_time.",
    "off_grid": "Move the time onto start_time plus an integer multiple of step_size.",
    "too_many_steps": "Increase step_size or shorten the run.",
    "unknown_variable": "Use a name from the FMU interface; see 'candidates' for near matches.",
    "unsupported_type": "The variable's declared type cannot be read or written here.",
    "out_of_range": "The value violates the FMU's declared min or max.",
    "not_writable": "Only inputs can be set at run time; parameters only in 'initial'.",
    "reserved_name": "'time' is the result timestamp; drop it from outputs.",
    "duplicate_output": "List each output once.",
    "empty_outputs": "Record at least one variable.",
    "duplicate_event": "Two events set the same variable at the same time; merge them.",
    "event_out_of_range": "Move the event to at/after start_time and before stop_time.",
    "duplicate_id": "Give each labelled item a unique id.",
    "no_action": "A rule needs 'set' assignments or stop=true.",
    "unknown_operator": "Use one of ==, !=, >, >=, <, <=.",
    "unsupported_operator": "Boolean and String conditions allow only == and !=.",
    "invalid_check": "A check needs a numeric variable listed in outputs and a min or max.",
}


def scenario_schema():
    """Return a copy of the JSON Schema for the scenario format."""
    return deepcopy(SCENARIO_SCHEMA)
