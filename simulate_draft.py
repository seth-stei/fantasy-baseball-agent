#!/usr/bin/env python3
"""
Headless Mock Draft Simulator
==============================
Runs a fully automated 23-round, 10-team draft — zero input required.
Sandy Koufax (pick 4) always takes the top-ranked AI suggestion.
Other teams pick near ADP with slight randomness to feel realistic.

Used to evaluate the quality of the draft logic before real draft day.

Usage:
  python3 simulate_draft.py              # position 4, 23 rounds, 10 teams
  python3 simulate_draft.py --pos 7      # different draft slot
  python3 simulate_draft.py --seed 42    # reproducible randomness
  python3 simulate_draft.py --quiet      # only show your picks + final team
"""
import os
import sys
import random
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from espn_agent.draft_data import build_player_pool, get_available, mark_drafted
from espn_agent.draft_suggester import (
    get_top_picks, format_my_turn_display, get_claude_pick_advice
)
from espn_agent.draft_tracker import get_snake_pick_numbers

MY_TEAM = 'Sandy Koufax'


def simulate(draft_position: int = 4, num_teams: int = 10, num_rounds: int = 23,
             seed: int = None, quiet: bool = False):

    if seed is not None:
        random.seed(seed)

    player_pool = build_player_pool()
    available = get_available(player_pool)

    my_pick_numbers = set(get_snake_pick_numbers(draft_position, num_teams, num_rounds))
    total_picks = num_teams * num_rounds

    # Team names (slot 0-indexed)
    teams = [f"Team {i}" for i in range(1, num_teams + 1)]
    teams[draft_position - 1] = MY_TEAM

    my_roster = []     # player dicts drafted by me
    draft_log = []     # (pick_num, round, player_name, team_name, is_mine)

    picks_done = 0

    print(f"\n{'═'*60}")
    print(f"  MOCK DRAFT SIMULATION  —  {MY_TEAM}  —  Pick #{draft_position}")
    print(f"  {num_teams} teams  ·  {num_rounds} rounds  ·  {total_picks} total picks")
    if seed is not None:
        print(f"  Random seed: {seed}")
    print(f"{'═'*60}\n")

    while picks_done < total_picks:
        pick_num  = picks_done + 1
        round_num = (picks_done // num_teams) + 1
        # Snake: which slot picks in this position?
        if round_num % 2 == 1:
            slot = (picks_done % num_teams)        # 0-indexed, normal order
        else:
            slot = num_teams - 1 - (picks_done % num_teams)  # reversed
        team_name = teams[slot]
        is_mine   = (pick_num in my_pick_numbers)

        if not available:
            break

        # ── My turn: take the top-ranked suggestion ────────────────────────────
        if is_mine:
            top = get_top_picks(available, my_roster, round_num, pick_num, n=6)
            if not top:
                picks_done += 1
                continue

            if not quiet:
                display = format_my_turn_display(top, my_roster, round_num,
                                                 pick_num, total_picks, MY_TEAM)
                print(display)
                advice = get_claude_pick_advice(top, my_roster, round_num, MY_TEAM)
                if advice and not advice.startswith('['):
                    print(f"\n  AI: {advice}")
                print()

            chosen = top[0]
            key = mark_drafted(player_pool, chosen['name'], MY_TEAM, pick_num, round_num)
            available = get_available(player_pool)
            if key and key in player_pool:
                my_roster.append(player_pool[key])

            pick_label = f"[R{round_num:02d} P{pick_num:03d}] ← YOU"
            print(f"  {pick_label}  {chosen['name']:<25}  ({'/'.join(chosen['positions'][:2])})")

        # ── Other teams: pick near ADP with slight randomness ─────────────────
        else:
            best = sorted(available.values(), key=lambda p: p['adp'])
            if not best:
                picks_done += 1
                continue
            # Other teams occasionally reach or fall for a player (realistic)
            idx = random.randint(0, min(4, len(best) - 1))
            picked = best[idx]
            key = mark_drafted(player_pool, picked['name'], team_name, pick_num, round_num)
            available = get_available(player_pool)

            if not quiet:
                print(f"  [R{round_num:02d} P{pick_num:03d}]  {picked['name']:<25}  →  {team_name}")

        draft_log.append((pick_num, round_num, picked['name'] if not is_mine else chosen['name'],
                          team_name, is_mine))
        picks_done += 1

    # ── Final roster display ───────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  {MY_TEAM.upper()} — FINAL ROSTER")
    print(f"{'═'*60}")

    hitters  = [p for p in my_roster if not p.get('is_pitcher')]
    pitchers = [p for p in my_roster if p.get('is_pitcher')]

    print("\n  HITTERS:")
    for p in hitters:
        pos   = '/'.join(p['positions'][:2])
        rnd   = p.get('round', '?')
        stats = []
        if p.get('proj_avg'):  stats.append(f".{int(p['proj_avg']*1000):03d} AVG")
        if p.get('proj_hr'):   stats.append(f"{p['proj_hr']} HR")
        if p.get('proj_rbi'):  stats.append(f"{p['proj_rbi']} RBI")
        if p.get('proj_r'):    stats.append(f"{p['proj_r']} R")
        if p.get('proj_sb'):   stats.append(f"{p['proj_sb']} SB")
        print(f"    R{rnd:>2}  {p['name']:<25} {pos:<8}  {', '.join(stats)}")

    print("\n  PITCHERS:")
    for p in pitchers:
        pos   = '/'.join(p['positions'][:2])
        rnd   = p.get('round', '?')
        stats = []
        if p.get('proj_era'):   stats.append(f"{p['proj_era']:.2f} ERA")
        if p.get('proj_whip'):  stats.append(f"{p['proj_whip']:.2f} WHIP")
        if p.get('proj_k'):     stats.append(f"{p['proj_k']} K")
        if p.get('proj_w'):     stats.append(f"{p['proj_w']} W")
        if p.get('proj_sv'):    stats.append(f"{p['proj_sv']} SV")
        print(f"    R{rnd:>2}  {p['name']:<25} {pos:<8}  {', '.join(stats)}")

    # ── Projected category totals ──────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  PROJECTED CATEGORY TOTALS (based on 2025 stats)")
    print(f"{'─'*60}")

    tot_hr  = sum(p.get('proj_hr', 0)  or 0 for p in hitters)
    tot_rbi = sum(p.get('proj_rbi', 0) or 0 for p in hitters)
    tot_r   = sum(p.get('proj_r', 0)   or 0 for p in hitters)
    tot_sb  = sum(p.get('proj_sb', 0)  or 0 for p in hitters)
    avg_vals = [p.get('proj_avg', 0) or 0 for p in hitters if p.get('proj_avg')]
    avg_team = sum(avg_vals) / len(avg_vals) if avg_vals else 0

    tot_k   = sum(p.get('proj_k', 0)  or 0 for p in pitchers)
    tot_w   = sum(p.get('proj_w', 0)  or 0 for p in pitchers)
    tot_sv  = sum(p.get('proj_sv', 0) or 0 for p in pitchers)
    era_vals  = [p.get('proj_era', 0)  or 0 for p in pitchers if p.get('proj_era')]
    whip_vals = [p.get('proj_whip', 0) or 0 for p in pitchers if p.get('proj_whip')]
    era_team  = sum(era_vals)  / len(era_vals)  if era_vals  else 0
    whip_team = sum(whip_vals) / len(whip_vals) if whip_vals else 0

    print(f"  HITTING   AVG: .{int(avg_team*1000):03d}   R: {tot_r}   HR: {tot_hr}   "
          f"RBI: {tot_rbi}   SB: {tot_sb}")
    print(f"  PITCHING  ERA: {era_team:.2f}   WHIP: {whip_team:.2f}   "
          f"K: {tot_k}   W: {tot_w}   SV: {tot_sv}")

    # ── Quick strength/weakness grade ─────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  CATEGORY GRADES  (rough benchmarks for competitive H2H team)")
    print(f"{'─'*60}")
    grades = [
        ("AVG",  avg_team,  .265, .275, "higher"),
        ("R",    tot_r,     700,  800,  "higher"),
        ("HR",   tot_hr,    180,  220,  "higher"),
        ("RBI",  tot_rbi,   600,  720,  "higher"),
        ("SB",   tot_sb,    80,   130,  "higher"),
        ("ERA",  era_team,  3.80, 3.40, "lower"),
        ("WHIP", whip_team, 1.25, 1.15, "lower"),
        ("K",    tot_k,     1200, 1500, "higher"),
        ("W",    tot_w,     65,   85,   "higher"),
        ("SV",   tot_sv,    40,   60,   "higher"),
    ]
    for cat, val, ok_thresh, good_thresh, direction in grades:
        if direction == "higher":
            grade = "★★★ ELITE" if val >= good_thresh else ("★★  SOLID" if val >= ok_thresh else "★   WEAK ")
        else:
            grade = "★★★ ELITE" if val <= good_thresh else ("★★  SOLID" if val <= ok_thresh else "★   WEAK ")
        if cat in ('AVG', 'ERA', 'WHIP'):
            val_str = f"{val:.3f}" if cat == 'AVG' else f"{val:.2f}"
        else:
            val_str = str(int(val))
        print(f"  {cat:<5}  {val_str:>7}   {grade}")

    print(f"\n{'═'*60}\n")


def main():
    parser = argparse.ArgumentParser(description='Headless mock draft simulator for evaluation')
    parser.add_argument('--pos',    type=int, default=4,  help='Your draft position (1-10)')
    parser.add_argument('--teams',  type=int, default=10, help='Number of teams')
    parser.add_argument('--rounds', type=int, default=23, help='Number of rounds')
    parser.add_argument('--seed',   type=int, default=None, help='Random seed for reproducibility')
    parser.add_argument('--quiet',  action='store_true', help='Only show your picks + final team')
    args = parser.parse_args()

    simulate(
        draft_position=args.pos,
        num_teams=args.teams,
        num_rounds=args.rounds,
        seed=args.seed,
        quiet=args.quiet,
    )


if __name__ == '__main__':
    main()
