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
from pathlib import Path

from fmpy.scenario_agent import TOOLS, authoring_context, call, run, run_many, validate

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

ANALYSIS_PROMPT_TEMPLATE = """다음은 FMU "{fmu}"에 대해 시나리오 {total}개를 실행한 결과다.
숫자는 모두 이미 계산되어 있으니 새로 계산하거나 추측하지 말고, 아래 표만 근거로
한국어로 짧게 설명해라.

{summary_line}

| 시나리오 | 결과 | 비고 |
|---|---|---|
{rows}

다음 세 가지를 설명해라:
1. 전체적으로 어떤 상태인가
2. 특히 주의가 필요한 시나리오가 있다면 어느 것이고 왜 그런가
   (판정 기준이 없는 시나리오는 실패가 아니라 관찰용이라는 점에 주의)
3. 다음에 무엇을 확인하면 좋은가

표에 없는 내용을 지어내지 마라.
"""

_NO_NOTE = "(없음)"


def _analysis_prompt(fmu, summary, results):
    """The same data format_batch_report() renders, reused as the model's only input."""
    rows = []
    for entry in results:
        if not entry.get("ok"):
            verdict, note = "오류", entry["errors"][0]["message"]
        else:
            passed = entry.get("passed")
            verdict = "합격" if passed is True else "불합격" if passed is False else "판정 기준 없음"
            report_summary = (entry.get("report") or {}).get("summary") or {}
            failed = report_summary.get("failed_checks") or []
            note = f"실패한 검사: {', '.join(failed)}" if failed else _NO_NOTE
        rows.append(f"| {entry['id']} | {verdict} | {note} |")
    return ANALYSIS_PROMPT_TEMPLATE.format(
        fmu=fmu, total=summary["total"], rows="\n".join(rows),
        summary_line=f"{summary['total']}개 중 합격 {summary['passed']} · "
                     f"불합격 {summary['failed']} · 판정 기준 없음 {summary['no_verdict']} · "
                     f"실행 불가 {summary['error']}")

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


def _ollama_generate(host, model, prompt, json_mode=True, timeout=120):
    """One call to Ollama's completion endpoint. JSON-shaped output by default
    (scenario generation); plain prose when ``json_mode`` is False (analysis)."""
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2}}
    if json_mode:
        body["format"] = "json"
    request = urllib.request.Request(f"{host}/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
                    include_samples=0, on_log=None, cancelled=None, api_key=None):
    """Let Claude call context/validate/run itself via tool-calling. One result envelope.

    ``api_key``, given, is passed to the client explicitly; omitted (the
    default), the Anthropic SDK reads ANTHROPIC_API_KEY from the environment
    on its own -- the usual way, and the only one the CLI scripts use.

    Raises ImportError if the ``anthropic`` package is not installed; the
    caller decides how to report that (the CLI and the GUI each word it
    differently).
    """
    import anthropic  # imported lazily: callers should work without it installed

    log = on_log or _NO_OP
    is_cancelled = cancelled or _NEVER
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
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


# ---------------------------------------------------------------------------
# Execution-and-analysis agent: given a folder, run every scenario in it
# (fmpy.scenario_agent.run_many -- deterministic, no model involved) and then
# ask a model to summarize the already-computed results in words. The model
# never runs anything and never sees a sample table; it only ever receives
# the same pass/fail/no-verdict/error table format_batch_report() renders,
# and is told not to invent numbers beyond it. This is a separate agent from
# generate_with_ollama/run_with_claude above: that one turns words into a
# scenario; this one turns a batch of already-run scenarios into words.
# ---------------------------------------------------------------------------

def _scenario_files(folder):
    return sorted(Path(folder).glob("*.json"))


def analyze_results_with_ollama(fmu, summary, results, host="http://localhost:11434",
                                model="gemma:2b", on_log=None):
    """Summarize an already-computed batch (e.g. from scenario_agent.run_many)
    in words. Returns (text, None) or (None, error_message); never raises for
    an unreachable server. Runs nothing -- ``summary``/``results`` are taken
    as given, exactly as format_batch_report() would render them."""
    log = on_log or _NO_OP
    log(f"[asking {model} to summarize {summary['total']} result(s)...]")
    try:
        text = _ollama_generate(host, model, _analysis_prompt(fmu, summary, results),
                                json_mode=False, timeout=180)
    except RuntimeError as error:
        log(str(error))
        return None, str(error)
    return text.strip(), None


def analyze_results_with_claude(fmu, summary, results, model="claude-sonnet-5", on_log=None,
                                api_key=None):
    """Same as analyze_results_with_ollama, asking Claude instead. No tool
    use: there is nothing left to run, only a table to read.

    Raises ImportError if the ``anthropic`` package is not installed.
    """
    import anthropic  # imported lazily: callers should work without it installed

    log = on_log or _NO_OP
    log(f"[asking {model} to summarize {summary['total']} result(s)...]")
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model, max_tokens=1024,
        messages=[{"role": "user", "content": _analysis_prompt(fmu, summary, results)}])
    text = "".join(block.text for block in response.content if block.type == "text")
    return text.strip(), None


def analyze_with_ollama(fmu, folder, host="http://localhost:11434", model="gemma:2b",
                        output=None, include_samples=0, on_log=None, on_progress=None,
                        cancelled=None):
    """Run every scenario in ``folder`` against ``fmu``, then ask Ollama to
    summarize the results in words. One envelope: the same shape as
    ``scenario_agent.run_many`` plus an ``"analysis"`` string (or None if the
    batch itself could not run, or the model could not be reached)."""
    log = on_log or _NO_OP
    paths = _scenario_files(folder)
    if not paths:
        return _fail("analyze", "no_scenarios_found", f"No .json files in {folder}")
    log(f"Running {len(paths)} scenario(s) from {folder}...")
    batch = run_many(fmu, paths, output=output, include_samples=include_samples,
                     on_progress=on_progress, cancelled=cancelled)
    if not batch["ok"] or (cancelled and cancelled()):
        return {**batch, "analysis": None}
    text, error = analyze_results_with_ollama(fmu, batch["summary"], batch["results"],
                                               host, model, on_log=log)
    return {**batch, "analysis": text, **({"analysis_error": error} if error else {})}


def analyze_with_claude(fmu, folder, model="claude-sonnet-5", output=None, include_samples=0,
                        on_log=None, on_progress=None, cancelled=None, api_key=None):
    """Same as analyze_with_ollama, asking Claude instead. No tool-calling here
    -- the batch already ran deterministically; the model only summarizes it.

    Raises ImportError if the ``anthropic`` package is not installed.
    """
    paths = _scenario_files(folder)
    if not paths:
        return _fail("analyze", "no_scenarios_found", f"No .json files in {folder}")
    log = on_log or _NO_OP
    log(f"Running {len(paths)} scenario(s) from {folder}...")
    batch = run_many(fmu, paths, output=output, include_samples=include_samples,
                     on_progress=on_progress, cancelled=cancelled)
    if not batch["ok"] or (cancelled and cancelled()):
        return {**batch, "analysis": None}
    text, error = analyze_results_with_claude(fmu, batch["summary"], batch["results"],
                                              model, on_log=log, api_key=api_key)
    return {**batch, "analysis": text, **({"analysis_error": error} if error else {})}
