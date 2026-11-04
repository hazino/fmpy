"""Derive scenarios from an FMU, run them, and print what came back.

This is the whole agent seam exercised without a model in the loop: the same
calls a language model would make, in the same order, with the same data. Use
it to check that a new FMU can be planned and run before wiring anything up.

    python examples/agent/plan_and_run.py path/to/model.fmu [--output results]
"""

import argparse
import json
from pathlib import Path

from fmpy.scenario_agent import plan, run


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fmu", type=Path)
    parser.add_argument("--output", type=Path,
                        help="Write results.csv, report.json and scenario.json per proposal")
    args = parser.parse_args()

    planned = plan(str(args.fmu))
    if not planned["ok"]:
        print(json.dumps(planned["errors"], indent=2, ensure_ascii=False))
        return 2

    declared = planned["structure"]
    print(f"{args.fmu.name}: {len(declared['declared_outputs'])} declared output(s), "
          f"{declared['continuous_states']} state(s), "
          f"{declared['event_indicators']} event indicator(s)")
    for output, edge in declared["dependencies"].items():
        drivers = "every known variable" if edge["depends_on"] is None else (
            ", ".join(edge["inputs"]) or "model state only")
        print(f"  {output} <- {drivers}")
    grid = planned["grid"]
    print(f"grid: {grid['steps']} steps of {grid['step_size']} s to {grid['stop_time']} s")

    failures = 0
    for proposal in planned["proposals"]:
        output = args.output / proposal["id"] if args.output else None
        executed = run(str(args.fmu), proposal["scenario"], output=output)
        if not executed["ok"]:
            failures += 1
            reason = executed["errors"][0]
            print(f"\n{proposal['id']}: FAILED {reason['code']} - {reason['message']}")
            continue
        summary = executed["report"]["summary"]
        print(f"\n{proposal['id']}: {summary['sample_count']} samples, "
              f"ended by {summary['termination']}, verdict {summary['passed']}")
        if summary["log_errors"] or summary["log_warnings"]:
            print(f"  FMU reported {summary['log_errors']} error(s), "
                  f"{summary['log_warnings']} warning(s)")
            for entry in executed["report"]["fmu_log"][:3]:
                print(f"    [{entry['status']}] t={entry['time']} {entry['message']}")
        for name, stats in summary["outputs"].items():
            if "min" in stats:
                print(f"  {name}: {stats['min']} .. {stats['max']} "
                      f"(ends at {stats['last']})")
            else:
                print(f"  {name}: {stats['first']} -> {stats['last']} "
                      f"({stats.get('change_count', 0)} change(s))")
        for want in proposal["needs_from_domain"]:
            print(f"  still needed: {want}")

    print(f"\n{len(planned['proposals']) - failures} of {len(planned['proposals'])} "
          "proposal(s) ran. No verdict is possible until checks are added:")
    for note in planned["notes"]:
        print(f"  - {note}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
