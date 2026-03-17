#!/usr/bin/env python3
"""
MLB Stats client - fetches today's schedule, injuries, and player stats.
Uses the free MLB Stats API (statsapi.mlb.com).
"""
import requests
from datetime import date, timedelta
from typing import Dict, List, Optional, Set


MLB_API_BASE = "https://statsapi.mlb.com/api/v1"


def get_todays_schedule() -> Dict:
    """
    Fetch today's MLB schedule.
    Returns dict mapping team ID -> {opponent, home_away, game_time, game_pk}
    """
    today = date.today().strftime('%Y-%m-%d')
    url = f"{MLB_API_BASE}/schedule"
    params = {
        'sportId': 1,
        'date': today,
        'hydrate': 'team,lineups'
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Warning: could not fetch MLB schedule: {e}")
        return {}

    teams_playing = {}
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            status = game.get('status', {}).get('abstractGameState', '')
            if status in ('Preview', 'Live', 'Final'):
                home = game['teams']['home']['team']
                away = game['teams']['away']['team']
                game_time = game.get('gameDate', '')
                game_pk = game.get('gamePk', '')

                teams_playing[home['id']] = {
                    'team_name': home['name'],
                    'home_away': 'home',
                    'opponent': away['name'],
                    'opponent_id': away['id'],
                    'game_time': game_time,
                    'game_pk': game_pk,
                }
                teams_playing[away['id']] = {
                    'team_name': away['name'],
                    'home_away': 'away',
                    'opponent': home['name'],
                    'opponent_id': home['id'],
                    'game_time': game_time,
                    'game_pk': game_pk,
                }
    return teams_playing


def get_mlb_team_map() -> Dict[str, int]:
    """
    Returns dict mapping team name/abbreviation variants -> MLB team ID.
    Used to match ESPN team names to MLB API team IDs.
    """
    url = f"{MLB_API_BASE}/teams"
    params = {'sportId': 1, 'season': date.today().year}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        teams = resp.json().get('teams', [])
    except Exception as e:
        print(f"  Warning: could not fetch MLB teams: {e}")
        return {}

    team_map = {}
    for team in teams:
        tid = team['id']
        team_map[team['name'].lower()] = tid
        team_map[team.get('abbreviation', '').lower()] = tid
        team_map[team.get('teamName', '').lower()] = tid
        team_map[team.get('shortName', '').lower()] = tid
    return team_map


def get_injury_report() -> Dict[str, str]:
    """
    Returns dict of player name -> injury status from MLB transactions.
    Note: ESPN's own injuryStatus field is more reliable for fantasy purposes.
    """
    # ESPN API provides injury status directly on players - using that is simpler
    # This is a fallback/supplement
    return {}


def get_recent_hitting_stats(days: int = 14) -> Dict[str, Dict]:
    """
    Fetch recent hitting stats from MLB API for all players.
    Returns dict: player_name -> {avg, hr, rbi, sb, r, ops, games}
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    url = f"{MLB_API_BASE}/stats"
    params = {
        'stats': 'byDateRange',
        'group': 'hitting',
        'startDate': start_date.strftime('%Y-%m-%d'),
        'endDate': end_date.strftime('%Y-%m-%d'),
        'sportId': 1,
        'limit': 500,
        'sortStat': 'ops'
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Warning: could not fetch hitting stats: {e}")
        return {}

    stats_map = {}
    for entry in data.get('stats', [{}])[0].get('splits', []):
        player = entry.get('player', {})
        stat = entry.get('stat', {})
        name = player.get('fullName', '')
        if name:
            stats_map[name.lower()] = {
                'avg': float(stat.get('avg', 0) or 0),
                'hr': int(stat.get('homeRuns', 0) or 0),
                'rbi': int(stat.get('rbi', 0) or 0),
                'sb': int(stat.get('stolenBases', 0) or 0),
                'r': int(stat.get('runs', 0) or 0),
                'ops': float(stat.get('ops', 0) or 0),
                'games': int(stat.get('gamesPlayed', 0) or 0),
            }
    return stats_map


def get_recent_pitching_stats(days: int = 14) -> Dict[str, Dict]:
    """
    Fetch recent pitching stats from MLB API.
    Returns dict: player_name -> {era, whip, k, wins, saves, ip}
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    url = f"{MLB_API_BASE}/stats"
    params = {
        'stats': 'byDateRange',
        'group': 'pitching',
        'startDate': start_date.strftime('%Y-%m-%d'),
        'endDate': end_date.strftime('%Y-%m-%d'),
        'sportId': 1,
        'limit': 300,
        'sortStat': 'strikeOuts'
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Warning: could not fetch pitching stats: {e}")
        return {}

    stats_map = {}
    for entry in data.get('stats', [{}])[0].get('splits', []):
        player = entry.get('player', {})
        stat = entry.get('stat', {})
        name = player.get('fullName', '')
        if name:
            ip = float(stat.get('inningsPitched', 0) or 0)
            stats_map[name.lower()] = {
                'era': float(stat.get('era', 99) or 99),
                'whip': float(stat.get('whip', 99) or 99),
                'k': int(stat.get('strikeOuts', 0) or 0),
                'wins': int(stat.get('wins', 0) or 0),
                'saves': int(stat.get('saves', 0) or 0),
                'ip': ip,
            }
    return stats_map


def get_games_this_week() -> Dict[int, int]:
    """
    Returns dict: mlb_team_id -> number of games scheduled this week.
    Useful for evaluating waiver wire pickups.
    """
    today = date.today()
    # Find the start of the current week (Monday)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    url = f"{MLB_API_BASE}/schedule"
    params = {
        'sportId': 1,
        'startDate': week_start.strftime('%Y-%m-%d'),
        'endDate': week_end.strftime('%Y-%m-%d'),
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Warning: could not fetch weekly schedule: {e}")
        return {}

    games_count = {}
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            for side in ['home', 'away']:
                team_id = game['teams'][side]['team']['id']
                games_count[team_id] = games_count.get(team_id, 0) + 1

    return games_count


if __name__ == '__main__':
    print("Fetching today's MLB schedule...")
    schedule = get_todays_schedule()
    print(f"Teams playing today: {len(schedule)}")
    for team_id, info in list(schedule.items())[:5]:
        print(f"  {info['team_name']} ({info['home_away']}) vs {info['opponent']}")

    print("\nFetching recent hitting stats...")
    hitting = get_recent_hitting_stats(days=14)
    print(f"Players with stats: {len(hitting)}")

    print("\n✓ Stats client working")
