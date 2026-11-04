"""Generate a scenario with a local Ollama model, validate it, and run it.

This is the actual wiring for the loop described in docs/agent-integration-ko.md:
a model only ever produces scenario JSON text; every native FMU call still goes
through fmpy.scenario_agent, never through the model. Nothing here executes
code the model wrote, and nothing here is optional plumbing -- it is the
real loop, run against a real local model.

    python examples/agent/ollama_loop.py --fmu model.fmu --request "..." \
        [--model qwen2.5] [--host http://localhost:11434] [--max-attempts 4] \
        [--output results/ollama-run]

Requires an Ollama server with a model pulled (``ollama pull <name>``). Tested
against Ollama 0.40.4 with ``gemma:2b`` -- a small, non-tool-calling model, so
this uses prompt-and-parse (format="json"), not Ollama's native tool-calling.
A model that does support tool-calling (e.g. qwen2.5) can use the same
scenario_agent.TOOLS manifest through Ollama's /api/chat `tools=` parameter
instead; see the README in this directory for that variant.

Exit codes match fmpy.scenario_agent.main: 0 ok and passed (or no checks),
1 ok but a check failed, 2 no valid scenario was produced, or the run failed.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

from fmpy.scenario_agent import authoring_context, run, validate

PROMPT_TEMPLATE = """You write test scenarios for an FMI 2.0 Co-Simulation FMU as a single JSON object.

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

FIX_TEMPLATE = """Your last JSON was rejected. Fix ONLY the listed problems and reply with the
corrected JSON object, still following all the rules above. Problems:
{errors}

Your last JSON was:
{previous}
"""


def ollama_generate(host, model, prompt):
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


def extract_json(text):
    """Ollama's format=json should return a bare object, but strip fences just in case."""
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def generate_scenario(fmu, request, host, model, max_attempts, log):
    """Prompt-and-validate loop. Returns a validated scenario or None."""
    context = authoring_context(fmu)
    if not context["ok"]:
        log(f"Cannot describe {fmu}: {context['errors'][0]['message']}")
        return None
    interface = context["interface"]
    prompt = PROMPT_TEMPLATE.format(
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
        log(f"[attempt {attempt}/{max_attempts}] asking {model}...")
        try:
            raw = ollama_generate(host, model, prompt)
        except RuntimeError as error:
            log(str(error))
            return None
        try:
            scenario = extract_json(raw)
        except (json.JSONDecodeError, AttributeError) as error:
            log(f"  model did not return parseable JSON ({error}); retrying")
            prompt = FIX_TEMPLATE.format(errors=f"- not valid JSON: {error}", previous=raw)
            continue
        checked = validate(fmu, scenario)
        if checked["ok"]:
            log(f"  valid after {attempt} attempt(s)")
            return scenario
        problems = "\n".join(f"- {e['path'] or '(top level)'}: {e['message']}"
                             for e in checked["errors"])
        log(f"  rejected:\n{problems}")
        prompt = FIX_TEMPLATE.format(errors=problems, previous=json.dumps(scenario))
    log(f"Gave up after {max_attempts} attempt(s).")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fmu", required=True)
    parser.add_argument("--request", required=True, help="What the scenario should do, in words")
    parser.add_argument("--model", default="gemma:2b")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--output", help="Directory to write results.csv/report.json/scenario.json")
    parser.add_argument("--quiet", action="store_true", help="Only print the final JSON report")
    args = parser.parse_args(argv)

    log = (lambda message: None) if args.quiet else (lambda message: print(message, file=sys.stderr))

    scenario = generate_scenario(args.fmu, args.request, args.host, args.model,
                                 args.max_attempts, log)
    if scenario is None:
        print(json.dumps({"ok": False, "tool": "ollama_loop",
                          "errors": [{"code": "no_valid_scenario",
                                     "message": "The model never produced a scenario that "
                                                "validated."}]}, indent=2, ensure_ascii=False))
        return 2

    result = run(args.fmu, scenario, output=args.output)
    print(json.dumps({**result, "generated_scenario": scenario}, indent=2, ensure_ascii=False))
    if not result["ok"]:
        return 2
    return 1 if result.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
