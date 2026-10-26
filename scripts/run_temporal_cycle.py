"""CLI script to execute a temporal cycle run from cron or command-line.

Usage:
    python scripts/run_temporal_cycle.py [--trigger {MANUAL,SCHEDULED}] [--dry-run]
"""
import argparse
import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.clock_service import clock_service
from app.services.temporal_cycle import run_temporal_cycle


def main():
    parser = argparse.ArgumentParser(description="Run FDR-Sentinel Temporal Lifecycle Automation Cycle")
    parser.add_argument(
        "--trigger",
        choices=["MANUAL", "SCHEDULED"],
        default="MANUAL",
        help="Trigger type (default: MANUAL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate transitions and alerts without persisting",
    )
    args = parser.parse_args()

    print(f"Executing temporal cycle (Trigger: {args.trigger}, Dry-Run: {args.dry_run})...")
    print(f"Authoritative Business Date (IST): {clock_service.today()}")

    run = run_temporal_cycle(trigger=args.trigger, dry_run=args.dry_run)

    if run is None:
        print("Skipped: another instance holds the temporal cycle advisory lock.")
        sys.exit(0)

    duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)

    print("\n" + "=" * 50)
    print("TEMPORAL CYCLE SUMMARY")
    print("=" * 50)
    print(f"Status:               {run.status}")
    print(f"Business Date:        {run.business_date}")
    print(f"Execution Duration:   {duration_ms} ms")
    print(f"Instruments Examined: {run.instruments_examined}")
    print(f"Instruments Skipped:  {run.instruments_skipped}")
    print(f"Transitions Applied:  {run.transitions_count}")
    print(f"Alerts Created:       {run.alerts_created}")
    print(f"Alerts Superseded:    {run.alerts_superseded}")
    print(f"Errors Count:         {run.errors_count}")

    if run.error_summary:
        print("\nErrors:")
        print(run.error_summary)
    print("=" * 50)

    if run.status == "FAILED":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
