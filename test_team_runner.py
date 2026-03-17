#!/usr/bin/env python3
"""
Scenario-Based Team Runner Evaluator
=====================================
Tests the real lineup optimizer + Claude AI analysis pipeline without
an ESPN connection or real season data.  Same philosophy as simulate_draft.py:
craft realistic situations and see how the system actually behaves.

Usage:
  python3 test_team_runner.py                        # normal scenario
  python3 test_team_runner.py --scenario injuries
  python3 test_team_runner.py --scenario hot_cold
  python3 test_team_runner.py --scenario off_days
  python3 test_team_runner.py --scenario trades
  python3 test_team_runner.py --scenario waivers
  python3 test_team_runner.py --scenario all          # run all 6 in sequence

  # User-supplied scenario via JSON file:
  python3 test_team_runner.py --scenario-file my_scenario.json
"""
import os
import sys
import json
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from espn_agent.lineup_optimizer import optimize_lineup, format_lineup_for_ai
from espn_agent.agent import (
    analyze_lineup, analyze_trades, analyze_waiver_wire,
    build_roster_summary, build_free_agents_summary,
)
from espn_agent.deliver import deliver

MY_TEAM = 'Sandy Koufax'
SCENARIOS = ['normal', 'injuries', 'hot_cold', 'off_days', 'trades', 'waivers']


# ── MockPlayer ────────────────────────────────────────────────────────────────

class MockPlayer:
    """
    Minimal ESPN-compatible player object for testing the optimizer and AI layer.

    Attributes mirror what lineup_optimizer.py reads from real ESPN player objects:
      .name          str   — player's full name
      .position      str   — primary slot (e.g. 'SP', 'OF', '1B')
      .proTeam       str   — MLB team abbreviation (e.g. 'NYY', 'LAD')
      .injuryStatus  str   — 'ACTIVE', 'OUT', 'DAY_TO_DAY', 'INJURY_RESERVE', etc.
      .eligibleSlots list  — which lineup slots this player can fill
      .stats         dict  — {'projected': {stat_key: value}}
      .percent_owned float — waiver wire display only
    """
    def __init__(self, name, position, pro_team, injury='ACTIVE',
                 stats=None, eligible=None, percent_owned=75.0):
        self.name = name
        self.position = position
        self.proTeam = pro_team
        self.injuryStatus = injury
        self.eligibleSlots = eligible or [position]
        self.stats = stats or {'projected': {}}
        self.percent_owned = percent_owned

    def __repr__(self):
        return f"MockPlayer({self.name}, {self.position}, {self.proTeam}, {self.injuryStatus})"


# ── Baseline data ─────────────────────────────────────────────────────────────

def build_baseline_roster():
    """
    Realistic 25-man roster for Sandy Koufax.
    Covers all lineup slots: C, 1B, 2B, 3B, SS, OF, UTIL, SP, RP, P.
    """
    return [
        # ── Hitters ──────────────────────────────────────────────────────────
        MockPlayer('Will Smith',       'C',  'LAD', eligible=['C']),
        MockPlayer('Freddie Freeman',  '1B', 'LAD', eligible=['1B']),
        MockPlayer('Jose Altuve',      '2B', 'HOU', eligible=['2B']),
        MockPlayer('Rafael Devers',    '3B', 'BOS', eligible=['3B']),
        MockPlayer('Francisco Lindor', 'SS', 'NYM', eligible=['SS']),
        MockPlayer('Juan Soto',        'OF', 'NYM', eligible=['OF']),
        MockPlayer('Kyle Tucker',      'OF', 'CHC', eligible=['OF']),
        MockPlayer('Cedric Mullins',   'OF', 'BAL', eligible=['OF']),
        MockPlayer('Yordan Alvarez',   'DH', 'HOU', eligible=['DH']),   # fills UTIL
        MockPlayer('Pete Alonso',      '1B', 'NYM', eligible=['1B']),   # bench / UTIL
        MockPlayer('Anthony Volpe',    'SS', 'NYY', eligible=['SS', '2B']),
        MockPlayer('Bryan Reynolds',   'OF', 'PIT', eligible=['OF']),
        MockPlayer('Brendan Donovan',  '2B', 'STL', eligible=['2B', '3B']),
        # ── Pitchers ─────────────────────────────────────────────────────────
        MockPlayer('Zack Wheeler',    'SP', 'PHI', eligible=['SP']),
        MockPlayer('Logan Gilbert',   'SP', 'SEA', eligible=['SP']),
        MockPlayer('Max Fried',       'SP', 'NYY', eligible=['SP']),
        MockPlayer('Dylan Cease',     'SP', 'SD',  eligible=['SP']),
        MockPlayer('Spencer Strider', 'SP', 'ATL', eligible=['SP']),
        MockPlayer('Emanuel Clase',   'RP', 'CLE', eligible=['RP']),
        MockPlayer('Josh Hader',      'RP', 'HOU', eligible=['RP']),
        MockPlayer('Pete Fairbanks',  'RP', 'TB',  eligible=['RP']),
        MockPlayer('Chris Sale',      'SP', 'ATL', eligible=['SP']),
        MockPlayer('Framber Valdez',  'SP', 'HOU', eligible=['SP']),
        MockPlayer('Clay Holmes',     'RP', 'NYM', eligible=['RP']),
        MockPlayer('David Bednar',    'RP', 'PIT', eligible=['RP']),
    ]


def build_baseline_teams_playing():
    """
    Which MLB teams have games today (keyed by lowercase abbreviation).
    player_has_game_today() checks: pro_team_lower in team_key or team_key in pro_team_lower.
    """
    return {
        'lad': {'home': 'LAD', 'away': 'SF'},
        'hou': {'home': 'HOU', 'away': 'TEX'},
        'bos': {'home': 'BOS', 'away': 'TOR'},
        'nym': {'home': 'NYM', 'away': 'MIA'},
        'chc': {'home': 'CHC', 'away': 'MIL'},
        'nyy': {'home': 'NYY', 'away': 'TB'},
        'tb':  {'home': 'NYY', 'away': 'TB'},
        'phi': {'home': 'PHI', 'away': 'ATL'},
        'atl': {'home': 'PHI', 'away': 'ATL'},
        'sea': {'home': 'SEA', 'away': 'OAK'},
        'cle': {'home': 'CLE', 'away': 'DET'},
        'bal': {'home': 'BAL', 'away': 'WSH'},
    }


def build_baseline_recent_hitting():
    """Recent 14-day hitting stats (keyed by lowercase player name).
    Uses actual league categories: avg, hr, xbh (extra base hits), rbi, sb, r.
    """
    return {
        'will smith':       {'avg': 0.285, 'hr': 3, 'xbh': 5,  'rbi': 10, 'sb': 0, 'r': 7,  'games': 12},
        'freddie freeman':  {'avg': 0.310, 'hr': 4, 'xbh': 7,  'rbi': 14, 'sb': 1, 'r': 11, 'games': 13},
        'jose altuve':      {'avg': 0.290, 'hr': 2, 'xbh': 4,  'rbi': 8,  'sb': 3, 'r': 10, 'games': 12},
        'rafael devers':    {'avg': 0.270, 'hr': 5, 'xbh': 9,  'rbi': 13, 'sb': 0, 'r': 8,  'games': 13},
        'francisco lindor': {'avg': 0.275, 'hr': 3, 'xbh': 5,  'rbi': 9,  'sb': 2, 'r': 10, 'games': 14},
        'juan soto':        {'avg': 0.300, 'hr': 4, 'xbh': 7,  'rbi': 11, 'sb': 1, 'r': 12, 'games': 13},
        'kyle tucker':      {'avg': 0.280, 'hr': 3, 'xbh': 5,  'rbi': 10, 'sb': 4, 'r': 9,  'games': 12},
        'cedric mullins':   {'avg': 0.265, 'hr': 2, 'xbh': 4,  'rbi': 7,  'sb': 5, 'r': 8,  'games': 13},
        'yordan alvarez':   {'avg': 0.305, 'hr': 5, 'xbh': 9,  'rbi': 15, 'sb': 0, 'r': 10, 'games': 12},
        'pete alonso':      {'avg': 0.255, 'hr': 4, 'xbh': 6,  'rbi': 11, 'sb': 0, 'r': 7,  'games': 13},
        'anthony volpe':    {'avg': 0.255, 'hr': 2, 'xbh': 4,  'rbi': 6,  'sb': 3, 'r': 8,  'games': 12},
        'bryan reynolds':   {'avg': 0.270, 'hr': 2, 'xbh': 4,  'rbi': 8,  'sb': 2, 'r': 7,  'games': 11},
        'brendan donovan':  {'avg': 0.290, 'hr': 1, 'xbh': 3,  'rbi': 6,  'sb': 2, 'r': 9,  'games': 12},
    }


def build_baseline_recent_pitching():
    """Recent 14-day pitching stats (keyed by lowercase player name).
    Uses actual league categories: era, whip, k, wins, qs, svhd (saves+holds), ip.
    """
    return {
        'zack wheeler':    {'era': 2.45, 'whip': 0.98, 'k': 22, 'wins': 2, 'qs': 2, 'svhd': 0, 'ip': 22.0},
        'logan gilbert':   {'era': 3.12, 'whip': 1.08, 'k': 19, 'wins': 1, 'qs': 1, 'svhd': 0, 'ip': 17.3},
        'max fried':       {'era': 2.88, 'whip': 1.05, 'k': 21, 'wins': 2, 'qs': 2, 'svhd': 0, 'ip': 21.7},
        'dylan cease':     {'era': 3.55, 'whip': 1.18, 'k': 24, 'wins': 1, 'qs': 1, 'svhd': 0, 'ip': 17.7},
        'spencer strider': {'era': 2.10, 'whip': 0.92, 'k': 28, 'wins': 2, 'qs': 2, 'svhd': 0, 'ip': 21.3},
        'emanuel clase':   {'era': 1.20, 'whip': 0.80, 'k': 8,  'wins': 0, 'qs': 0, 'svhd': 5, 'ip': 7.5},
        'josh hader':      {'era': 0.90, 'whip': 0.75, 'k': 11, 'wins': 0, 'qs': 0, 'svhd': 6, 'ip': 10.0},
        'pete fairbanks':  {'era': 2.50, 'whip': 1.05, 'k': 9,  'wins': 0, 'qs': 0, 'svhd': 3, 'ip': 7.2},
        'chris sale':      {'era': 3.20, 'whip': 1.10, 'k': 18, 'wins': 1, 'qs': 1, 'svhd': 0, 'ip': 16.7},
        'framber valdez':  {'era': 3.40, 'whip': 1.20, 'k': 16, 'wins': 1, 'qs': 1, 'svhd': 0, 'ip': 18.0},
        'clay holmes':     {'era': 2.80, 'whip': 1.10, 'k': 7,  'wins': 0, 'qs': 0, 'svhd': 4, 'ip': 6.1},
        'david bednar':    {'era': 3.60, 'whip': 1.25, 'k': 8,  'wins': 0, 'qs': 0, 'svhd': 2, 'ip': 5.0},
    }


# ── Built-in scenario builders ────────────────────────────────────────────────

def scenario_normal():
    """Full roster, most teams playing, standard stats. Baseline test."""
    return (
        build_baseline_roster(),
        build_baseline_teams_playing(),
        {},
        build_baseline_recent_hitting(),
        build_baseline_recent_pitching(),
        {},
    )


def scenario_injuries():
    """
    Lindor (SS) is OUT, Strider (SP) is INJURY_RESERVE, Will Smith (C) is DAY_TO_DAY.
    Tests: optimizer skips all three, AI addresses the gaps.
    """
    roster = build_baseline_roster()
    for p in roster:
        if p.name == 'Francisco Lindor':
            p.injuryStatus = 'OUT'
        elif p.name == 'Spencer Strider':
            p.injuryStatus = 'INJURY_RESERVE'
        elif p.name == 'Will Smith':
            p.injuryStatus = 'DAY_TO_DAY'
    return (
        roster,
        build_baseline_teams_playing(),
        {},
        build_baseline_recent_hitting(),
        build_baseline_recent_pitching(),
        {},
    )


def scenario_hot_cold():
    """
    Soto is on fire (.390/8 HR last 14 days), Alonso is ice cold (.145/0 HR),
    Clase is unhittable (0.50 ERA, 7 SV), Gilbert is getting shelled (6.80 ERA).
    Tests: optimizer ranks hot players higher; AI calls out the streaks.
    """
    roster = build_baseline_roster()
    recent_hitting = build_baseline_recent_hitting()
    recent_pitching = build_baseline_recent_pitching()

    # On fire
    recent_hitting['juan soto'] = {
        'avg': 0.390, 'hr': 8, 'xbh': 13, 'rbi': 18, 'sb': 3, 'r': 16, 'games': 14
    }
    recent_hitting['freddie freeman'] = {
        'avg': 0.360, 'hr': 6, 'xbh': 10, 'rbi': 16, 'sb': 1, 'r': 14, 'games': 13
    }
    recent_pitching['emanuel clase'] = {
        'era': 0.50, 'whip': 0.60, 'k': 12, 'wins': 0, 'qs': 0, 'svhd': 7, 'ip': 9.0
    }

    # Ice cold
    recent_hitting['pete alonso'] = {
        'avg': 0.145, 'hr': 0, 'xbh': 0, 'rbi': 2, 'sb': 0, 'r': 2, 'games': 14
    }
    recent_pitching['logan gilbert'] = {
        'era': 6.80, 'whip': 1.65, 'k': 9, 'wins': 0, 'qs': 0, 'svhd': 0, 'ip': 10.0
    }

    return (roster, build_baseline_teams_playing(), {}, recent_hitting, recent_pitching, {})


def scenario_off_days():
    """
    Only 5 teams play today (BOS, PHI/ATL series, NYY/TB series).
    Most hitters have no game — tests optimizer bench logic and AI explanation.
    Players with games: Devers (BOS), Volpe (NYY), Wheeler/Sale/Strider (PHI/ATL), Fried (NYY), Fairbanks (TB).
    """
    roster = build_baseline_roster()
    limited_schedule = {
        'bos': {'home': 'BOS', 'away': 'TOR'},
        'phi': {'home': 'PHI', 'away': 'ATL'},
        'atl': {'home': 'PHI', 'away': 'ATL'},
        'nyy': {'home': 'NYY', 'away': 'TB'},
        'tb':  {'home': 'NYY', 'away': 'TB'},
    }
    # LAD, HOU, NYM, CHC, BAL, SEA, CLE all off today
    return (
        roster, limited_schedule, {},
        build_baseline_recent_hitting(), build_baseline_recent_pitching(), {}
    )


def scenario_trades():
    """
    Normal lineup, but the team is ranked 9th in SB and 10th (last) in K.
    Tests: analyze_trades() identifies those weaknesses and proposes targeted trades.
    """
    return (
        build_baseline_roster(),
        build_baseline_teams_playing(),
        {},
        build_baseline_recent_hitting(),
        build_baseline_recent_pitching(),
        {'weak_categories': ['K', 'SB'], 'run_trades': True},
    )


def scenario_waivers():
    """
    Normal roster + 15 free agents. Parker Meadows is a hot streaker at only 12% owned.
    Tests: analyze_waiver_wire() surfaces the obvious pickup.
    """
    free_agents = [
        MockPlayer('Jackson Chourio',  'OF', 'MIL', percent_owned=45.0),
        MockPlayer('Christian Yelich', 'OF', 'MIL', percent_owned=38.0),
        MockPlayer('Parker Meadows',   'OF', 'DET', percent_owned=12.0),   # obvious add
        MockPlayer('Ryan Noda',        '1B', 'OAK', percent_owned=8.0),
        MockPlayer('Rowdy Tellez',     '1B', 'MIL', percent_owned=22.0),
        MockPlayer('Gavin Lux',        '2B', 'LAD', percent_owned=18.0),
        MockPlayer('Nico Hoerner',     '2B', 'CHC', percent_owned=55.0),
        MockPlayer('Michael Busch',    '2B', 'CHC', percent_owned=30.0),
        MockPlayer('Ian Happ',         'OF', 'CHC', percent_owned=48.0),
        MockPlayer('Alec Bohm',        '3B', 'PHI', percent_owned=62.0),
        MockPlayer('Bryce Miller',     'SP', 'SEA', percent_owned=55.0),
        MockPlayer('MacKenzie Gore',   'SP', 'WAS', percent_owned=42.0),
        MockPlayer('Jordan Romano',    'RP', 'TOR', percent_owned=35.0),
        MockPlayer('Raisel Iglesias',  'RP', 'LAA', percent_owned=28.0),
        MockPlayer('Jose Alvarado',    'RP', 'PHI', percent_owned=18.0),
    ]

    recent_hitting = build_baseline_recent_hitting()
    recent_hitting['parker meadows'] = {
        'avg': 0.375, 'hr': 5, 'xbh': 8, 'rbi': 12, 'sb': 4, 'r': 11, 'games': 12
    }

    return (
        build_baseline_roster(),
        build_baseline_teams_playing(),
        {},
        recent_hitting,
        build_baseline_recent_pitching(),
        {'free_agents': free_agents, 'weak_categories': ['OF depth', 'SB'], 'run_waivers': True},
    )


# ── JSON scenario overrides ───────────────────────────────────────────────────

def apply_json_overrides(roster, teams_playing, recent_hitting, recent_pitching, overrides: dict) -> str:
    """
    Apply user-supplied JSON overrides on top of baseline data (in place).
    Returns the scenario name/label.

    Supported fields (all optional):
      injuries       {player_name: status_string}
      hot_players    {player_name: {stat: value, ...}}
      cold_players   {player_name: {stat: value, ...}}
      no_game_teams  [team_abbrev, ...]
      weak_categories [cat, ...]
      name           str  (display label)
      description    str
      notes          str
    """
    # Injuries
    for player_name, status in overrides.get('injuries', {}).items():
        for p in roster:
            if p.name.lower() == player_name.lower():
                p.injuryStatus = status

    # Hot players — override / merge recent hitting stats
    for player_name, stats in overrides.get('hot_players', {}).items():
        key = player_name.lower()
        if key not in recent_hitting:
            recent_hitting[key] = {
                'avg': 0.280, 'hr': 2, 'xbh': 4, 'rbi': 8, 'sb': 1, 'r': 7, 'games': 10
            }
        recent_hitting[key].update(stats)
        recent_hitting[key].setdefault('games', 10)

    # Cold players — override / merge with slump numbers
    for player_name, stats in overrides.get('cold_players', {}).items():
        key = player_name.lower()
        if key not in recent_hitting:
            recent_hitting[key] = {
                'avg': 0.200, 'hr': 0, 'xbh': 0, 'rbi': 2, 'sb': 0, 'r': 3, 'games': 10
            }
        recent_hitting[key].update(stats)
        recent_hitting[key].setdefault('games', 10)

    # Remove teams with no game today
    for abbrev in overrides.get('no_game_teams', []):
        key = abbrev.lower()
        teams_playing.pop(key, None)

    return overrides.get('name', 'custom')


def run_json_scenario(path: str):
    """Load and run a user-supplied JSON scenario file."""
    with open(path) as f:
        overrides = json.load(f)

    roster       = build_baseline_roster()
    teams_playing = build_baseline_teams_playing()
    recent_hitting = build_baseline_recent_hitting()
    recent_pitching = build_baseline_recent_pitching()

    label = apply_json_overrides(roster, teams_playing, recent_hitting, recent_pitching, overrides)

    extras = {}
    if overrides.get('weak_categories'):
        extras['weak_categories'] = overrides['weak_categories']
        extras['run_trades'] = True

    header_notes = ' | '.join(filter(None, [
        overrides.get('description', ''),
        overrides.get('notes', ''),
    ]))

    run_scenario(label, roster, teams_playing, {}, recent_hitting, recent_pitching,
                 extras, notes=header_notes)


# ── Core runner ───────────────────────────────────────────────────────────────

def _build_trade_context(roster, weak_categories):
    """Build the strings needed by analyze_trades()."""
    roster_summary = build_roster_summary(roster)

    cats = ', '.join(weak_categories) if weak_categories else 'none identified'
    category_standings = (
        "Category rankings (1 = best in league, 10 = worst):\n"
        "  AVG: 3rd    R: 4th    HR: 5th    RBI: 4th\n"
        f"  SB:  9th  (WEAK)    ERA: 3rd    WHIP: 2nd\n"
        f"  K:  10th  (WEAK)    W:   5th    SV:   3rd\n"
        f"\nWeakest categories: {cats}"
    )
    other_teams = (
        "\nTeam 2 (strong in K and SB):\n"
        "  Shohei Ohtani  (SP/DH)  — 245 K projected\n"
        "  Ronald Acuña Jr. (OF)   —  60 SB projected\n"
        "  Manny Machado  (3B)     — AVG surplus\n"
        "  Nestor Cortes  (SP)     — possible trade chip\n"
        "\nTeam 5 (strong in K, weak in AVG):\n"
        "  Gerrit Cole    (SP)     — 220 K projected\n"
        "  Luis Castillo  (SP)     — 200 K projected\n"
        "  Jose Miranda   (3B)     — .228 AVG drag\n"
    )
    return roster_summary, category_standings, other_teams


def run_scenario(label: str, roster, teams_playing, mlb_team_map,
                 recent_hitting, recent_pitching, extras: dict, notes: str = ''):
    """
    Run the full pipeline (optimizer → AI lineup → optional AI trade/waiver) for one scenario.
    Calls deliver() with send_email=False so output stays in the terminal.
    """
    print(f"\n{'═'*60}")
    print(f"  SCENARIO: {label.upper()}")
    if notes:
        print(f"  {notes}")
    print(f"{'═'*60}")

    # 1. Optimize lineup
    lineup_result = optimize_lineup(
        roster, teams_playing, mlb_team_map, recent_hitting, recent_pitching
    )
    lineup_text = format_lineup_for_ai(lineup_result)

    # 2. AI lineup analysis
    print("\n  [Calling Claude for lineup analysis...]")
    lineup_analysis = analyze_lineup(lineup_text, MY_TEAM)

    # 3. Trade analysis (trades scenario or JSON with weak_categories)
    trade_analysis = ''
    if extras.get('run_trades'):
        weak_cats = extras.get('weak_categories', ['K', 'SB'])
        roster_summary, category_standings, other_teams = _build_trade_context(roster, weak_cats)
        print("  [Calling Claude for trade analysis...]")
        trade_analysis = analyze_trades(MY_TEAM, roster_summary, category_standings, other_teams)

    # 4. Waiver wire analysis
    waiver_analysis = ''
    if extras.get('run_waivers'):
        free_agents = extras.get('free_agents', [])
        roster_summary = build_roster_summary(roster)
        fa_summary = build_free_agents_summary(free_agents)
        weak_cats = extras.get('weak_categories', ['SB', 'OF depth'])
        category_needs = ', '.join(weak_cats)
        print("  [Calling Claude for waiver wire analysis...]")
        waiver_analysis = analyze_waiver_wire(MY_TEAM, fa_summary, roster_summary, category_needs)

    # 5. Print digest (no email)
    deliver(
        team_name=MY_TEAM,
        lineup_text=lineup_text,
        lineup_analysis=lineup_analysis,
        trade_analysis=trade_analysis,
        waiver_analysis=waiver_analysis,
        send_email=False,
    )


# ── Scenario registry ─────────────────────────────────────────────────────────

SCENARIO_MAP = {
    'normal':   scenario_normal,
    'injuries': scenario_injuries,
    'hot_cold': scenario_hot_cold,
    'off_days': scenario_off_days,
    'trades':   scenario_trades,
    'waivers':  scenario_waivers,
}

SCENARIO_NOTES = {
    'normal':   'Full roster, 12 teams playing, standard recent stats',
    'injuries': 'Lindor OUT · Strider INJURY_RESERVE · Will Smith DAY_TO_DAY',
    'hot_cold': 'Soto .390/8 HR (fire) · Alonso .145 (ice) · Clase 0.50 ERA · Gilbert 6.80 ERA',
    'off_days': 'Only BOS/PHI/ATL/NYY/TB play today — most hitters idle',
    'trades':   'Ranked 9th in SB and last (10th) in K — trade advice needed',
    'waivers':  'Parker Meadows streaking (.375/5 HR) at just 12% owned',
}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Scenario-based team runner evaluator for Sandy Koufax'
    )
    parser.add_argument(
        '--scenario', default='normal',
        choices=SCENARIOS + ['all'],
        help='Built-in scenario to run (default: normal)',
    )
    parser.add_argument(
        '--scenario-file', dest='scenario_file', default=None,
        help='Path to a JSON scenario file (overrides --scenario)',
    )
    args = parser.parse_args()

    if args.scenario_file:
        run_json_scenario(args.scenario_file)
        return

    to_run = SCENARIOS if args.scenario == 'all' else [args.scenario]

    for name in to_run:
        fn = SCENARIO_MAP[name]
        roster, teams_playing, mlb_team_map, recent_hitting, recent_pitching, extras = fn()
        run_scenario(
            label=name,
            roster=roster,
            teams_playing=teams_playing,
            mlb_team_map=mlb_team_map,
            recent_hitting=recent_hitting,
            recent_pitching=recent_pitching,
            extras=extras,
            notes=SCENARIO_NOTES.get(name, ''),
        )

    print(f"\n{'═'*60}")
    if args.scenario == 'all':
        print(f"  ALL {len(to_run)} SCENARIOS COMPLETE")
    print(f"{'═'*60}\n")


if __name__ == '__main__':
    main()
