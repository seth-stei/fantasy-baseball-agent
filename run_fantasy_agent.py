#!/usr/bin/env python3
"""
ESPN Fantasy Baseball Agent - Daily Entry Point

Usage:
    python3 run_fantasy_agent.py              # Full briefing + set lineup on ESPN
    python3 run_fantasy_agent.py --dry-run    # Briefing only, don't set lineup
    python3 run_fantasy_agent.py --trades     # Include trade analysis
    python3 run_fantasy_agent.py --waivers    # Include waiver wire analysis
    python3 run_fantasy_agent.py --no-email   # Skip email, print only
    python3 run_fantasy_agent.py --test       # Test ESPN connection only
"""
import os
import sys
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from espn_agent.espn_client import ESPNClient
from espn_agent.stats_client import (
    get_todays_schedule,
    get_mlb_team_map,
    get_recent_hitting_stats,
    get_recent_pitching_stats,
)
from espn_agent.lineup_optimizer import optimize_lineup, format_lineup_for_ai
from espn_agent.agent import (
    analyze_lineup,
    analyze_trades,
    analyze_waiver_wire,
    build_roster_summary,
    build_category_standings_summary,
    build_other_teams_summary,
    build_free_agents_summary,
    build_il_alert,
)
from espn_agent.deliver import deliver
from espn_agent.stats_client import get_games_this_week


def run(dry_run=False, include_trades=False, include_waivers=False, send_email=True):
    print("="*60)
    print("FANTASY BASEBALL AGENT STARTING")
    print("="*60)

    # 1. Connect to ESPN
    print("\n[1/5] Connecting to ESPN...")
    try:
        espn = ESPNClient()
        print(f"  ✓ Connected - Team: {espn.my_team.team_name}")
        print(f"  ✓ Record: {espn.my_team.wins}W - {espn.my_team.losses}L")
    except Exception as e:
        print(f"  ✗ ESPN connection failed: {e}")
        print("\n  Make sure ESPN_S2, ESPN_SWID, and ESPN_LEAGUE_ID are in your .env")
        sys.exit(1)

    # 2. Fetch today's MLB schedule
    print("\n[2/5] Fetching today's MLB schedule...")
    try:
        teams_playing, confirmed_starters = get_todays_schedule()
        mlb_team_map = get_mlb_team_map()
        print(f"  ✓ {len(teams_playing)} teams playing today  ·  "
              f"{len(confirmed_starters)} confirmed SP starters")
    except Exception as e:
        print(f"  ⚠️  Could not fetch schedule: {e}")
        teams_playing = {}
        mlb_team_map = {}
        confirmed_starters = set()

    # 3. Fetch recent player stats
    print("\n[3/5] Fetching recent player stats (last 14 days)...")
    try:
        recent_hitting = get_recent_hitting_stats(days=14)
        recent_pitching = get_recent_pitching_stats(days=14)
        print(f"  ✓ {len(recent_hitting)} hitters, {len(recent_pitching)} pitchers")
    except Exception as e:
        print(f"  ⚠️  Could not fetch stats: {e}")
        recent_hitting = {}
        recent_pitching = {}

    # 4. Build lineup recommendation
    print("\n[4/5] Building lineup recommendation...")
    roster = espn.get_my_roster()
    matchup = espn.get_matchup()

    lineup_result = optimize_lineup(
        roster=roster,
        teams_playing=teams_playing,
        mlb_team_map=mlb_team_map,
        recent_hitting=recent_hitting,
        recent_pitching=recent_pitching,
        confirmed_starters=confirmed_starters,
    )

    lineup_text = format_lineup_for_ai(lineup_result, matchup)
    lineup_analysis = analyze_lineup(
        lineup_text=lineup_text,
        team_name=espn.my_team.team_name,
    )
    print("  ✓ Lineup analysis complete")

    # 5. Optional: trade and waiver analysis
    trade_analysis = ''
    waiver_analysis = ''

    if include_trades:
        print("\n  Analyzing trade opportunities...")
        all_teams = espn.get_all_teams()
        trade_analysis = analyze_trades(
            my_team_name=espn.my_team.team_name,
            my_roster_summary=build_roster_summary(roster),
            category_standings=build_category_standings_summary(all_teams, espn.my_team),
            other_teams_summary=build_other_teams_summary(all_teams, espn.my_team),
        )

    if include_waivers:
        print("\n  Analyzing waiver wire...")
        free_agents = espn.get_free_agents(size=30)
        waiver_analysis = analyze_waiver_wire(
            my_team_name=espn.my_team.team_name,
            available_players=build_free_agents_summary(free_agents),
            my_roster_summary=build_roster_summary(roster),
            category_needs="Based on recent matchup performance",
        )

    # Build matchup score string
    matchup_score = ''
    if matchup:
        try:
            if matchup.home_team == espn.my_team:
                opp = matchup.away_team.team_name
                my_score = matchup.home_score
                opp_score = matchup.away_score
            else:
                opp = matchup.home_team.team_name
                my_score = matchup.away_score
                opp_score = matchup.home_score
            matchup_score = f"{espn.my_team.team_name} {my_score} - {opp_score} {opp}"
        except Exception:
            pass

    # Set lineup + run waiver check before delivering so results appear in the email
    roster_moves = ''
    il_alerts = ''
    roster_entries = []

    if not dry_run:
        print("\n[5a] Setting lineup on ESPN...")
        try:
            from espn_agent.lineup_setter import set_lineup, get_team_id, fetch_league_data
            team_id = get_team_id()
            roster_entries, scoring_period = fetch_league_data(team_id)
            success = set_lineup(lineup_result['starters'])
            if success:
                print("  ✓ Lineup set on ESPN")
            else:
                print("  ✗ Lineup set failed (ESPN rejected the request)")
        except ImportError:
            print("  ⚠️  Lineup setter not yet built (run with --dry-run to skip)")
        except Exception as e:
            print(f"  ✗ Failed to set lineup: {e}")

        print("\n[5b] Running waiver check...")
        try:
            from espn_agent.roster_manager import run_waiver_check
            games_this_week = get_games_this_week()
            roster_moves = run_waiver_check(
                espn_client=espn,
                recent_hitting=recent_hitting,
                recent_pitching=recent_pitching,
                confirmed_starters=confirmed_starters,
                games_this_week=games_this_week,
                dry_run=False,
            )
        except Exception as e:
            print(f"  ⚠️  Waiver check failed: {e}")
            roster_moves = f"Waiver check error: {e}"
    else:
        print("\n(Dry run - lineup not set on ESPN, waiver check skipped)")

    # IL alerts — works in both dry-run and live mode (read-only)
    try:
        if not roster_entries:
            from espn_agent.lineup_setter import get_team_id, fetch_league_data
            team_id = get_team_id()
            roster_entries, _ = fetch_league_data(team_id)
        il_alerts = build_il_alert(roster, roster_entries)
        if il_alerts:
            print(f"\n{il_alerts}")
    except Exception as e:
        print(f"  ⚠️  IL alert check failed: {e}")

    # Deliver digest
    print("\n[5/5] Delivering briefing...")
    deliver(
        team_name=espn.my_team.team_name,
        lineup_text=lineup_text,
        lineup_analysis=lineup_analysis,
        trade_analysis=trade_analysis,
        waiver_analysis=waiver_analysis,
        roster_moves=roster_moves,
        il_alerts=il_alerts,
        matchup_score=matchup_score,
        send_email=send_email,
    )


def test_connection():
    """Test ESPN connection and print league info."""
    print("Testing ESPN connection...\n")
    try:
        espn = ESPNClient()
        espn.get_league_summary()

        print(f"\nRoster ({len(espn.get_my_roster())} players):")
        for player in espn.get_my_roster():
            status = getattr(player, 'injuryStatus', 'ACTIVE') or 'ACTIVE'
            status_str = f" [{status}]" if status != 'ACTIVE' else ''
            pos = getattr(player, 'position', '')
            print(f"  {player.name:<28} {pos}{status_str}")

        print("\n✓ ESPN connection successful!")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("\nMake sure ESPN_S2, ESPN_SWID, and ESPN_LEAGUE_ID are in your .env")
        sys.exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ESPN Fantasy Baseball Agent')
    parser.add_argument('--dry-run', action='store_true',
                        help='Generate briefing but do not set lineup on ESPN')
    parser.add_argument('--trades', action='store_true',
                        help='Include trade opportunity analysis')
    parser.add_argument('--waivers', action='store_true',
                        help='Include waiver wire analysis')
    parser.add_argument('--no-email', action='store_true',
                        help='Print to terminal only, skip email')
    parser.add_argument('--test', action='store_true',
                        help='Test ESPN connection only')
    args = parser.parse_args()

    if args.test:
        test_connection()
    else:
        run(
            dry_run=args.dry_run,
            include_trades=args.trades,
            include_waivers=args.waivers,
            send_email=not args.no_email,
        )
