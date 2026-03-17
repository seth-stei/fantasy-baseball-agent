#!/usr/bin/env python3
"""
Weekly Fantasy Baseball Agent — Monday Morning Pitcher Streaming
================================================================
Runs every Monday to grab two-start SPs off the wire before the week locks in.

Usage:
    python3 run_weekly_agent.py              # Live run — executes adds/drops
    python3 run_weekly_agent.py --dry-run    # Preview moves, nothing posted
"""
import os
import sys
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from espn_agent.espn_client import ESPNClient
from espn_agent.pitcher_streamer import run_weekly_streaming
from espn_agent.deliver import send_email_digest
from datetime import date


def main():
    parser = argparse.ArgumentParser(description='Weekly pitcher streaming agent')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview moves without posting to ESPN')
    parser.add_argument('--no-email', action='store_true',
                        help='Skip email, print to terminal only')
    args = parser.parse_args()

    print("=" * 60)
    print(f"  WEEKLY PITCHER STREAMER  {'(DRY RUN)' if args.dry_run else ''}")
    print(f"  {date.today().strftime('%A, %B %-d, %Y')}")
    print("=" * 60)

    # Connect to ESPN
    print("\n[1/2] Connecting to ESPN...")
    try:
        espn = ESPNClient()
        print(f"  ✓ Team: {espn.my_team.team_name}  "
              f"({espn.my_team.wins}W - {espn.my_team.losses}L)")
    except Exception as e:
        print(f"  ✗ ESPN connection failed: {e}")
        sys.exit(1)

    # Run streaming
    print("\n[2/2] Running weekly pitcher streaming...")
    summary = run_weekly_streaming(espn, dry_run=args.dry_run)
    print(f"\n{summary}")

    # Email summary
    if not args.no_email and not args.dry_run:
        today = date.today().strftime('%B %-d')
        digest = (
            f"{'='*60}\n"
            f"WEEKLY PITCHER STREAMING — {today}\n"
            f"{espn.my_team.team_name}\n"
            f"{'='*60}\n\n"
            f"{summary}\n\n"
            f"{'='*60}"
        )
        send_email_digest(digest, subject=f"Fantasy BB Streaming Moves — {today}")

    print(f"\n{'='*60}\n")


if __name__ == '__main__':
    main()
