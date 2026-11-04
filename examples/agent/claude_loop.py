"""Generate a scenario with Claude's native tool-calling, validate it, and run it.

Same loop as ollama_loop.py in this directory, but using the Anthropic Messages
API's tool-calling instead of prompt-and-parse: the model decides when to call
context/validate/run itself, this script only executes the matching
fmpy.scenario_agent function and feeds the (JSON) result back as a tool
result. The model never touches the FMU directly.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python examples/agent/claude_loop.py --fmu model.fmu --request "..." \
        [--model claude-sonnet-5] [--output results/claude-run]

NOT executed end to end in this repository's own verification: this sandbox
has neither the `anthropic` package nor ANTHROPIC_API_KEY. It was written
against the stable Messages API tool-use shape and checked with `python -m
py_compile`, but has not been run against the live API. Treat it as a
starting point and verify the first run yourself. ollama_loop.py in this same
directory IS verified end to end, against a locally running model -- see its
docstring and docs/agent-integration-ko.md for what that run actually showed.

Exit codes match fmpy.scenario_agent.main: 0 ok and passed (or no checks),
1 ok but a check failed, 2 the model never produced a valid scenario, or the
run failed.
"""

import argparse
import json
import sys

from fmpy.scenario_agent import TOOLS, call

SYSTEM_PROMPT = """You write test scenarios for FMI 2.0 Co-Simulation FMUs.
Call `context` first to see the FMU's interface, the JSON Schema and a working
example. Write one scenario JSON object matching that schema. Call `validate`
on it; if it comes back with errors, fix exactly the listed problems (by
`code` and `path`) and validate again. Once it validates, call `run` and
report the result in one short sentence -- do not print the JSON yourself,
the tool result already contains it."""


def _as_tools(manifest):
    """Anthropic's tool shape: {name, description, input_schema}."""
    return [{"name": t["name"], "description": t["description"],
            "input_schema": t["parameters"]} for t in manifest]


def run_loop(fmu, request, model, max_turns, log):
    import anthropic  # imported lazily: this script should still --help without it installed

    client = anthropic.Anthropic()
    messages = [{"role": "user",
                "content": f"FMU: {fmu}\n\nTask: {request}"}]
    last_run_result = None

    for turn in range(1, max_turns + 1):
        log(f"[turn {turn}/{max_turns}] asking {model}...")
        response = client.messages.create(
            model=model, max_tokens=4096, system=SYSTEM_PROMPT,
            tools=_as_tools(TOOLS), messages=messages)
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [block for block in response.content if block.type == "tool_use"]
        if not tool_calls:
            text = "".join(block.text for block in response.content if block.type == "text")
            log(f"  model finished without calling a tool: {text}")
            break

        results = []
        for block in tool_calls:
            log(f"  -> {block.name}({json.dumps(block.input)})")
            tool_result = call(block.name, fmu=fmu, **{k: v for k, v in block.input.items()
                                                       if k != "fmu"})
            if block.name == "run" and tool_result.get("ok"):
                last_run_result = tool_result
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(tool_result, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})

        if response.stop_reason != "tool_use":
            break

    return last_run_result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fmu", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    log = (lambda message: None) if args.quiet else (lambda message: print(message, file=sys.stderr))

    try:
        result = run_loop(args.fmu, args.request, args.model, args.max_turns, log)
    except ImportError:
        print(json.dumps({"ok": False, "tool": "claude_loop",
                          "errors": [{"code": "missing_dependency",
                                     "message": "pip install anthropic"}]}))
        return 2

    if result is None:
        print(json.dumps({"ok": False, "tool": "claude_loop",
                          "errors": [{"code": "no_valid_scenario",
                                     "message": "The model never completed a successful run."}]}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
