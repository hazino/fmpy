"""Generate-validate-fix-run loops against a local Ollama model or Claude.

This is library code, not a demo: ``examples/agent/ollama_loop.py`` and
``examples/agent/claude_loop.py`` are thin CLIs around it, and
``fmpy.gui.agent_chat`` is a GUI around it. In every case the model only ever
produces scenario JSON text (or, for Claude, decides which
``fmpy.scenario_agent`` tool to call next); every native FMU call still goes
through ``fmpy.scenario_agent``, never through the model.

Two backends, because they were verified differently:

- Ollama: prompt-and-parse (``format="json"``), because the model tested here
  (``gemma:2b``) does not support tool-calling. Verified end to end against a
  real local Ollama server -- see docs/agent-integration-ko.md for what that
  run actually showed (the loop works; a 2B model's instruction-following
  does not).
- Claude: native tool-calling via the Anthropic Messages API, passing
  ``fmpy.scenario_agent.TOOLS`` directly as ``input_schema``. Not run against
  the live API from this repository (no package or API key in that
  environment) -- only ``py_compile``-checked. Needs a first real run
  wherever it is used with credentials.
"""

import json
import re
import urllib.error
import urllib.request

from fmpy.scenario_agent import TOOLS, authoring_context, call, run, validate

OLLAMA_PROMPT_TEMPLATE = """You write test scenarios for an FMI 2.0 Co-Simulation FMU as a single JSON object.

The FMU's interface (only these variables exist; use no others):
{variables}

The scenario JSON must have this shape (only use these top-level keys):
{example}

Rules:
- "initial" may only set names from this list: {writable_initial}
- any "set" (in "events" or "rules") may only use names from this list: {writable_runtime}
- "outputs" may only list names from this list: {readable}
- stop_time and every event "time" must be a multiple of step_size
- reply with ONLY the JSON object, no explanation, no markdown fences

Task: {request}
"""

OLLAMA_FIX_TEMPLATE = """Your last JSON was rejected. Fix ONLY the listed problems and reply with the
corrected JSON object, still following all the rules above. Problems:
{errors}

Your last JSON was:
{previous}
"""

CLAUDE_SYSTEM_PROMPT = """You write test scenarios for FMI 2.0 Co-Simulation FMUs.
Call `context` first to see the FMU's interface, the JSON Schema and a working
example. Write one scenario JSON object matching that schema. Call `validate`
on it; if it comes back with errors, fix exactly the listed problems (by
`code` and `path`) and validate again. Once it validates, call `run` and
report the result in one short sentence -- do not print the JSON yourself,
the tool result already contains it."""

_NO_OP = lambda message: None
_NEVER = lambda: False


def _fail(tool, code, message, **extra):
    return {"ok": False, "tool": tool, "errors": [{"code": code, "message": message, **extra}]}


def ollama_models(host="http://localhost:11434", timeout=3):
    """Names of the models this Ollama server has pulled, or [] if unreachable."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as response:
            return [m["name"] for m in json.loads(response.read())["models"]]
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
        return []


def _ollama_generate(host, model, prompt):
    """One call to Ollama's completion endpoint, forcing JSON-shaped output."""
    body = json.dumps({"model": model, "prompt": prompt, "format": "json",
                       "stream": False, "options": {"temperature": 0.2}}).encode()
    request = urllib.request.Request(f"{host}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())["response"]
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach Ollama at {host}: {error}") from error


def _extract_json(text):
    """Ollama's format=json should return a bare object, but strip fences just in case."""
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def generate_with_ollama(fmu, request, host="http://localhost:11434", model="gemma:2b",
                         max_attempts=4, on_log=None, cancelled=None):
    """Prompt-and-validate loop. Returns a validated scenario dict, or None."""
    log = on_log or _NO_OP
    is_cancelled = cancelled or _NEVER
    context = authoring_context(fmu)
    if not context["ok"]:
        log(f"Cannot describe {fmu}: {context['errors'][0]['message']}")
        return None
    interface = context["interface"]
    prompt = OLLAMA_PROMPT_TEMPLATE.format(
        variables="\n".join(f"- {v['name']} ({v['type']}, {v['causality']}): "
                            f"{v['description'] or 'no description'}"
                            for v in interface["variables"]),
        example=json.dumps(context["example"]),
        writable_initial=interface["writable_initial"],
        writable_runtime=interface["writable_runtime"],
        readable=interface["readable"],
        request=request)

    scenario, raw = None, None
    for attempt in range(1, max_attempts + 1):
        if is_cancelled():
            log("Cancelled.")
            return None
        log(f"[attempt {attempt}/{max_attempts}] asking {model}...")
        try:
            raw = _ollama_generate(host, model, prompt)
        except RuntimeError as error:
            log(str(error))
            return None
        try:
            scenario = _extract_json(raw)
        except (json.JSONDecodeError, AttributeError) as error:
            log(f"  model did not return parseable JSON ({error}); retrying")
            prompt = OLLAMA_FIX_TEMPLATE.format(errors=f"- not valid JSON: {error}", previous=raw)
            continue
        checked = validate(fmu, scenario)
        if checked["ok"]:
            log(f"  valid after {attempt} attempt(s)")
            return scenario
        problems = "\n".join(f"- {e['path'] or '(top level)'}: {e['message']}"
                             for e in checked["errors"])
        log(f"  rejected:\n{problems}")
        prompt = OLLAMA_FIX_TEMPLATE.format(errors=problems, previous=json.dumps(scenario))
    log(f"Gave up after {max_attempts} attempt(s).")
    return None


def run_with_ollama(fmu, request, host="http://localhost:11434", model="gemma:2b",
                    max_attempts=4, output=None, include_samples=0, on_log=None, cancelled=None):
    """Generate a scenario with Ollama, then validate and run it. One result envelope.

    ``include_samples`` is forwarded to ``scenario_agent.run`` as-is; pass a
    large number (e.g. the scenario's own step count) to get every recorded
    row back for plotting, not just a prefix.
    """
    scenario = generate_with_ollama(fmu, request, host, model, max_attempts, on_log, cancelled)
    if scenario is None:
        return _fail("ollama_loop", "no_valid_scenario",
                    "The model never produced a scenario that validated.")
    result = run(fmu, scenario, output=output, include_samples=include_samples)
    return {**result, "generated_scenario": scenario}


def _claude_tools():
    """Anthropic's tool shape: {name, description, input_schema}."""
    return [{"name": t["name"], "description": t["description"],
            "input_schema": t["parameters"]} for t in TOOLS]


def run_with_claude(fmu, request, model="claude-sonnet-5", max_turns=8, output=None,
                    include_samples=0, on_log=None, cancelled=None):
    """Let Claude call context/validate/run itself via tool-calling. One result envelope.

    Raises ImportError if the ``anthropic`` package is not installed; the
    caller decides how to report that (the CLI and the GUI each word it
    differently).
    """
    import anthropic  # imported lazily: callers should work without it installed

    log = on_log or _NO_OP
    is_cancelled = cancelled or _NEVER
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"FMU: {fmu}\n\nTask: {request}"}]
    last_run_result, last_scenario = None, None

    for turn in range(1, max_turns + 1):
        if is_cancelled():
            log("Cancelled.")
            break
        log(f"[turn {turn}/{max_turns}] asking {model}...")
        response = client.messages.create(
            model=model, max_tokens=4096, system=CLAUDE_SYSTEM_PROMPT,
            tools=_claude_tools(), messages=messages)
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [block for block in response.content if block.type == "tool_use"]
        if not tool_calls:
            text = "".join(block.text for block in response.content if block.type == "text")
            log(f"  model finished without calling a tool: {text}")
            break

        results = []
        for block in tool_calls:
            arguments = dict(block.input)
            if block.name == "run":
                if output is not None:
                    arguments.setdefault("output", output)
                if include_samples:
                    arguments.setdefault("include_samples", include_samples)
            log(f"  -> {block.name}({json.dumps(arguments)})")
            tool_result = call(block.name, fmu=fmu, **{k: v for k, v in arguments.items()
                                                       if k != "fmu"})
            if block.name == "run" and tool_result.get("ok"):
                last_run_result, last_scenario = tool_result, arguments.get("scenario")
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(tool_result, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})

        if response.stop_reason != "tool_use":
            break

    if last_run_result is None:
        return _fail("claude_loop", "no_valid_scenario",
                    "The model never completed a successful run.")
    return {**last_run_result, "generated_scenario": last_scenario}
