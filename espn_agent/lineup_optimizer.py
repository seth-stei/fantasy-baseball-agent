#!/usr/bin/env python3
"""
Lineup optimizer for H2H category fantasy baseball.
Suggests the optimal daily lineup based on who has games, injury status,
and recent/projected performance.
"""
from typing import List, Dict, Tuple, Optional


# ESPN position slot names -> eligible player positions
# Configured for Sandy Koufax's league:
#   Hitters: C, 1B, 2B, 3B, SS, 3×OF, UTIL, 5 Bench, 3 IL
#   Pitchers: 7×P (no SP/RP distinction)
LINEUP_SLOTS = {
    'C':    ['C'],
    '1B':   ['1B'],
    '2B':   ['2B'],
    '3B':   ['3B'],
    'SS':   ['SS'],
    'OF':   ['LF', 'CF', 'RF', 'OF'],
    'OF2':  ['LF', 'CF', 'RF', 'OF'],
    'OF3':  ['LF', 'CF', 'RF', 'OF'],
    'UTIL': ['1B', '2B', '3B', 'SS', 'C', 'LF', 'CF', 'RF', 'OF', 'DH'],
    'P':    ['SP', 'RP'],   # all 7 pitcher slots accept either SP or RP
    'P2':   ['SP', 'RP'],
    'P3':   ['SP', 'RP'],
    'P4':   ['SP', 'RP'],
    'P5':   ['SP', 'RP'],
    'P6':   ['SP', 'RP'],
    'P7':   ['SP', 'RP'],
}

# Injury statuses that mean "don't start"
INACTIVE_STATUSES = {'OUT', 'INJURY_RESERVE', 'DAY_TO_DAY', 'FIFTEEN_DAY_DL',
                     'SIXTY_DAY_DL', 'SUSPENDED', 'NA', 'INJURY_RESERVE'}

# Hitting categories (H2H): R, HR, XBH, RBI, SB, AVG
HIT_CATS = ['avg', 'r', 'hr', 'rbi', 'sb', 'xbh']
# Pitching categories (H2H): K, QS, W, ERA, WHIP, SVHD  (no CG)
PIT_CATS = ['era', 'whip', 'k', 'wins', 'qs', 'svhd']


def is_player_available(player) -> bool:
    """Check if a player is healthy and startable."""
    status = getattr(player, 'injuryStatus', 'ACTIVE')
    if not status:
        return True
    return status.upper() not in INACTIVE_STATUSES


def get_player_pro_team(player) -> Optional[str]:
    """Extract the player's MLB team name from ESPN player object."""
    try:
        return player.proTeam
    except AttributeError:
        return None


def player_has_game_today(player, teams_playing: Dict, mlb_team_map: Dict) -> bool:
    """
    Check if a player's MLB team is playing today.
    teams_playing: {team_name_lower: game_info} from stats_client
    mlb_team_map: {team_name_lower: team_id}
    """
    pro_team = get_player_pro_team(player)
    if not pro_team:
        return True  # Assume they play if we can't check

    pro_team_lower = pro_team.lower()

    # Check direct match in teams_playing (keyed by team name)
    for team_key in teams_playing:
        if pro_team_lower in team_key or team_key in pro_team_lower:
            return True

    return False


def score_hitter(player, recent_stats: Dict) -> float:
    """
    Score a hitter based on recent stats. Higher = better.
    Used to rank eligible hitters for lineup slots.
    """
    name_lower = player.name.lower()
    stats = recent_stats.get(name_lower, {})

    if not stats or stats.get('games', 0) < 3:
        # Fall back to ESPN projected stats
        try:
            proj = player.stats.get('projected', {}) or {}
            avg = float(proj.get('avg', 0) or 0)
            hr = float(proj.get('HR', 0) or 0)
            rbi = float(proj.get('RBI', 0) or 0)
            sb = float(proj.get('SB', 0) or 0)
            r = float(proj.get('R', 0) or 0)
            # XBH proxy from projected HR (HR are a subset of XBH; multiply by 1.6 for doubles/triples)
            xbh_est = hr * 1.6
            return (avg * 50) + (xbh_est * 2.5) + (hr * 3) + (rbi * 2) + (sb * 3) + (r * 1.5)
        except Exception:
            return 0.0

    # Weight recent performance against actual league categories
    avg = stats.get('avg', 0)
    hr = stats.get('hr', 0)
    rbi = stats.get('rbi', 0)
    sb = stats.get('sb', 0)
    r = stats.get('r', 0)
    xbh = stats.get('xbh', 0)

    return (avg * 50) + (xbh * 4) + (hr * 3) + (rbi * 2) + (sb * 3) + (r * 1.5)


def score_pitcher(player, recent_stats: Dict, has_game_today: bool,
                  is_confirmed_starter: bool = False) -> float:
    """
    Score a pitcher for start/sit decisions.

    is_confirmed_starter: True if this SP is confirmed via MLB API as today's probable starter.
    - Confirmed SP starter → full quality score
    - SP with game today but NOT confirmed → small holdover score (might relieve)
    - SP with no game → 0
    - RP scoring is unchanged (relievers don't get a per-day "confirmed" flag)
    """
    name_lower = player.name.lower()
    stats = recent_stats.get(name_lower, {})
    position = getattr(player, 'position', '')

    # Relievers: score on K rate, ERA, saves+holds (SVHD) — game availability only
    if 'RP' in str(position):
        if not stats:
            return 3.0 if has_game_today else 0.0
        k = stats.get('k', 0)
        era = stats.get('era', 4.5)
        svhd = stats.get('svhd', 0)
        score = (k * 0.5) + (svhd * 2.5) + max(0, 5 - era)
        return score if has_game_today else 0.0

    # Starting pitchers: require confirmed start for full score
    if not has_game_today:
        return 0.0

    # Compute quality score from recent or projected stats
    if not stats or stats.get('ip', 0) < 5:
        try:
            proj = player.stats.get('projected', {}) or {}
            era = float(proj.get('ERA', 4.5) or 4.5)
            k = float(proj.get('K', 0) or 0)
            quality = max(0, (6 - era) * 3 + k * 0.1)
        except Exception:
            quality = 2.0
    else:
        era = stats.get('era', 4.5)
        whip = stats.get('whip', 1.35)
        k = stats.get('k', 0)
        wins = stats.get('wins', 0)
        qs = stats.get('qs', 0)
        quality = max(0, (6 - era) * 3) + max(0, (1.5 - whip) * 5) + (k * 0.2) + (qs * 3) + (wins * 1.5)

    if is_confirmed_starter:
        return quality          # Full score — pitching today
    else:
        return quality * 0.15  # Team has game but not confirmed starter; prefer confirmed arms


def get_eligible_positions(player) -> List[str]:
    """Get all positions a player is eligible to fill."""
    try:
        return player.eligibleSlots or [player.position]
    except AttributeError:
        pos = getattr(player, 'position', 'UTIL')
        return [pos]


def optimize_lineup(
    roster: List,
    teams_playing: Dict,
    mlb_team_map: Dict,
    recent_hitting: Dict,
    recent_pitching: Dict,
    confirmed_starters: set = None,
) -> Dict:
    """
    Build the optimal lineup for today.

    Returns:
        {
            'starters': {slot: player},
            'bench': [players],
            'injured_out': [players],
            'no_game': [players],
            'notes': [str]
        }
    """
    confirmed_starters = confirmed_starters or set()
    notes = []
    injured = []
    no_game = []
    available_hitters = []
    available_pitchers = []

    # Categorize all rostered players
    for player in roster:
        if not is_player_available(player):
            injured.append(player)
            continue

        has_game = player_has_game_today(player, teams_playing, mlb_team_map)
        position = getattr(player, 'position', '')

        is_pitcher = 'SP' in str(position) or 'RP' in str(position)

        if is_pitcher:
            is_confirmed = player.name.lower() in confirmed_starters
            score = score_pitcher(player, recent_pitching, has_game,
                                  is_confirmed_starter=is_confirmed)
            available_pitchers.append((player, score, has_game))
        else:
            if not has_game:
                no_game.append(player)
                continue
            score = score_hitter(player, recent_hitting)
            available_hitters.append((player, score))

    # Sort by score descending
    available_hitters.sort(key=lambda x: x[1], reverse=True)
    available_pitchers.sort(key=lambda x: x[1], reverse=True)

    starters = {}
    used_players = set()

    # Fill hitting slots in order of specificity (most specific first)
    hitting_slots = ['C', '1B', '2B', '3B', 'SS', 'OF', 'OF2', 'OF3', 'UTIL']

    for slot in hitting_slots:
        eligible_positions = LINEUP_SLOTS[slot]
        best_player = None
        best_score = -1

        for player, score in available_hitters:
            if id(player) in used_players:
                continue
            player_positions = get_eligible_positions(player)
            if any(pos in eligible_positions for pos in player_positions):
                if score > best_score:
                    best_player = player
                    best_score = score

        if best_player:
            starters[slot] = (best_player, best_score)
            used_players.add(id(best_player))
        else:
            starters[slot] = None
            notes.append(f"⚠️  No eligible player for {slot}")

    # Fill pitching slots (7 P slots — no SP/RP distinction in this league)
    pit_slots_order = ['P', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7']
    for slot in pit_slots_order:
        eligible_positions = LINEUP_SLOTS[slot]
        best_player = None
        best_score = -1

        for player, score, has_game in available_pitchers:
            if id(player) in used_players:
                continue
            player_positions = get_eligible_positions(player)
            if any(pos in eligible_positions for pos in player_positions):
                if score > best_score:
                    best_player = player
                    best_score = score

        if best_player:
            starters[slot] = (best_player, best_score)
            used_players.add(id(best_player))

    # Everyone not starting goes to bench
    bench_hitters = [p for p, s in available_hitters if id(p) not in used_players]
    bench_pitchers = [p for p, s, h in available_pitchers if id(p) not in used_players]

    return {
        'starters': starters,
        'bench': bench_hitters + bench_pitchers,
        'injured_out': injured,
        'no_game': no_game,
        'notes': notes,
    }


def format_lineup_for_ai(lineup_result: Dict, matchup=None) -> str:
    """
    Format lineup result into a text prompt for Claude to interpret.
    """
    lines = []

    if matchup:
        try:
            opponent = (matchup.away_team if matchup.home_team.team_name else matchup.home_team)
            lines.append(f"Current matchup opponent: {opponent.team_name}")
            lines.append(f"Current score: {matchup.home_score} - {matchup.away_score}\n")
        except Exception:
            pass

    lines.append("RECOMMENDED STARTERS:")
    for slot, value in lineup_result['starters'].items():
        if value:
            player, score = value
            status = getattr(player, 'injuryStatus', 'ACTIVE') or 'ACTIVE'
            lines.append(f"  {slot:<6} {player.name:<25} (score: {score:.1f}) [{status}]")
        else:
            lines.append(f"  {slot:<6} EMPTY - no eligible player")

    if lineup_result['bench']:
        lines.append("\nBENCH:")
        for p in lineup_result['bench']:
            lines.append(f"  {p.name}")

    if lineup_result['injured_out']:
        lines.append("\nINJURED/OUT:")
        for p in lineup_result['injured_out']:
            status = getattr(p, 'injuryStatus', 'OUT')
            lines.append(f"  {p.name} [{status}]")

    if lineup_result['no_game']:
        lines.append("\nNO GAME TODAY:")
        for p in lineup_result['no_game']:
            lines.append(f"  {p.name}")

    if lineup_result['notes']:
        lines.append("\nNOTES:")
        for note in lineup_result['notes']:
            lines.append(f"  {note}")

    return '\n'.join(lines)
