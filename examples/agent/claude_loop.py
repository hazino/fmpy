"""Generate a scenario with Claude's native tool-calling, validate it, and run it.

Same loop as ollama_loop.py in this directory, but using the Anthropic Messages
API's tool-calling instead of prompt-and-parse: the model decides when to call
context/validate/run itself, this script only executes the matching
fmpy.scenario_agent function and feeds the (JSON) result back as a tool
result. The model never touches the FMU directly. The loop itself lives in
fmpy.scenario_backends.run_with_claude; this is a thin CLI around it. The
same function backs the "에이전트에게 요청..." chat dialog in the GUI
(fmpy.gui.agent_chat).

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

from fmpy.scenario_backends import run_with_claude


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fmu", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--output", help="Directory to write results.csv/report.json/scenario.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    log = (lambda message: None) if args.quiet else (lambda message: print(message, file=sys.stderr))

    try:
        result = run_with_claude(args.fmu, args.request, args.model, args.max_turns,
                                 args.output, on_log=log)
    except ImportError:
        print(json.dumps({"ok": False, "tool": "claude_loop",
                          "errors": [{"code": "missing_dependency",
                                     "message": "pip install anthropic"}]}))
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        return 2
    return 1 if result.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
