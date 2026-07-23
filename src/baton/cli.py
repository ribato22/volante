from __future__ import annotations

import argparse

# The four routing objectives (§6.2); the CLI default is cash_protect_quota (§7.2).
_PREFER_CHOICES = ("cash_protect_quota", "quality", "local", "cheap")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="baton",
        description="Orchestrate GOAL across your configured models.",
    )
    parser.add_argument("goal", help="the objective to orchestrate")
    parser.add_argument(
        "--prefer",
        choices=_PREFER_CHOICES,
        default="cash_protect_quota",
        help="routing objective (default: cash_protect_quota)",
    )
    parser.add_argument(
        "--provider",
        "-P",
        default=None,
        help="restrict the planner/synth baseline to this provider name",
    )
    parser.add_argument("--model", default=None, help="override the planner/synth model_id")
    parser.add_argument(
        "--json", action="store_true", help="print the run summary as one JSON line"
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="disable live streaming of plan/worker/synth text",
    )
    return parser.parse_args(argv)
