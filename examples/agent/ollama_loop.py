"""Generate a scenario with a local Ollama model, validate it, and run it.

This is the actual wiring for the loop described in docs/agent-integration-ko.md:
a model only ever produces scenario JSON text; every native FMU call still goes
through fmpy.scenario_agent, never through the model. Nothing here executes
code the model wrote, and nothing here is optional plumbing -- it is the
real loop, run against a real local model. The loop itself lives in
fmpy.scenario_backends.run_with_ollama; this is a thin CLI around it. The
same function backs the "에이전트에게 요청..." chat dialog in the GUI
(fmpy.gui.agent_chat).

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
import sys

from fmpy.scenario_backends import run_with_ollama


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

    result = run_with_ollama(args.fmu, args.request, args.host, args.model,
                             args.max_attempts, args.output, on_log=log)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        return 2
    return 1 if result.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
