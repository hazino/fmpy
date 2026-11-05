"""Run every scenario in a folder, then ask a model to summarize the results.

This is the execution-and-analysis agent, deliberately separate from
ollama_loop.py/claude_loop.py (the generation agent, which turns words into a
new scenario). This one takes scenarios that already exist -- e.g.
examples/agent/pid_demo/scenarios/ -- runs all of them against one FMU
(fmpy.scenario_agent.run_many, fully deterministic, no model involved), and
only then hands the already-computed pass/fail table to a model and asks for
a short natural-language summary. The model never runs anything and never
sees a sample table, only the same summary a human would read.

    python examples/agent/analyze_scenarios.py --fmu model.fmu \
        --folder path/to/scenarios [--backend ollama|claude] [--model NAME] \
        [--host http://localhost:11434] [--output results/analysis]

Verified against examples/agent/pid_demo/PID.fmu and its seven scenarios with
a local Ollama server (gemma:2b): the execution and the tally were exactly
right (5 passed, 1 no-verdict, 1 refused at init, matching
tests/test_pid_demo.py). The model's own prose summary was not reliable --
it miscounted which scenarios passed. That is a model-quality limit, not a
pipeline bug; a stronger model (a tool-calling-capable Ollama model, or
Claude) should read the same table correctly. See
docs/agent-integration-ko.md for what was actually observed.

Exit codes: 0 every scenario that ran passed or had no verdict, 1 at least
one failed, 2 at least one could not run (or no .json files were found).
"""

import argparse
import json
import sys

from fmpy.scenario_backends import analyze_with_claude, analyze_with_ollama


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fmu", required=True)
    parser.add_argument("--folder", required=True, help="Directory of scenario *.json files")
    parser.add_argument("--backend", choices=["ollama", "claude"], default="ollama")
    parser.add_argument("--model")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--output", help="Directory for one subfolder per scenario + summary.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    log = (lambda message: None) if args.quiet else (lambda message: print(message, file=sys.stderr))

    try:
        if args.backend == "ollama":
            envelope = analyze_with_ollama(args.fmu, args.folder, host=args.host,
                                           model=args.model or "gemma:2b",
                                           output=args.output, on_log=log)
        else:
            envelope = analyze_with_claude(args.fmu, args.folder,
                                           model=args.model or "claude-sonnet-5",
                                           output=args.output, on_log=log)
    except ImportError:
        print(json.dumps({"ok": False, "tool": "analyze",
                          "errors": [{"code": "missing_dependency",
                                     "message": "pip install anthropic"}]}))
        return 2

    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    if not envelope["ok"]:
        return 2
    summary = envelope["summary"]
    if summary["error"]:
        return 2
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
