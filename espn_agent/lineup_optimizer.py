#!/usr/bin/env python3
"""
Lineup optimizer for H2H category fantasy baseball.
Suggests the optimal daily lineup based on who has games, injury status,
and recent/projected performance.
"""
import csv
import os
import re
from typing import List, Dict, Tuple, Optional


def _normalize_name(name: str) -> str:
    """Lowercase, strip name suffixes (Jr., Sr., II, III, IV), collapse whitespace."""
    name = name.lower().strip()
    name = re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv)\b', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


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
    'P':    ['SP', 'RP', 'P'],   # all 7 pitcher slots accept either SP or RP
    'P2':   ['SP', 'RP', 'P'],
    'P3':   ['SP', 'RP', 'P'],
    'P4':   ['SP', 'RP', 'P'],
    'P5':   ['SP', 'RP', 'P'],
    'P6':   ['SP', 'RP', 'P'],
    'P7':   ['SP', 'RP', 'P'],
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

    # Resolve team name to MLB team ID via mlb_team_map, then check teams_playing (keyed by ID)
    team_id = mlb_team_map.get(pro_team_lower)
    if team_id is not None:
        return team_id in teams_playing

    # Fallback: substring match against team_name values in teams_playing
    for info in teams_playing.values():
        team_name = info.get('team_name', '').lower()
        if pro_team_lower in team_name or team_name in pro_team_lower:
            return True

    return False


def score_hitter(player, recent_stats: Dict, adp_map: Dict = None) -> float:
    """
    Score a hitter based on recent stats. Higher = better.
    Falls back to ESPN projected stats, then ADP rank.
    """
    name_lower = player.name.lower()
    stats = recent_stats.get(name_lower, {})

    if not stats or stats.get('games', 0) < 3:
        # Fall back to ESPN projected stats
        try:
            proj = player.stats.get('projected', {}) or {}
            avg = float(proj.get('avg', 0) or proj.get('AVG', 0) or 0)
            hr = float(proj.get('hr', 0) or proj.get('HR', 0) or 0)
            rbi = float(proj.get('rbi', 0) or proj.get('RBI', 0) or 0)
            sb = float(proj.get('sb', 0) or proj.get('SB', 0) or 0)
            r = float(proj.get('r', 0) or proj.get('R', 0) or 0)
            # XBH proxy from projected HR (HR are a subset of XBH; multiply by 1.6 for doubles/triples)
            xbh_est = hr * 1.6
            proj_score = (avg * 50) + (xbh_est * 2.5) + (hr * 3) + (rbi * 2) + (sb * 3) + (r * 1.5)
        except Exception:
            proj_score = 0.0
        if proj_score > 0:
            return proj_score
        # Fall back to ADP rank as proxy for player quality
        if adp_map:
            return adp_score(player, adp_map)
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
    eligible = get_eligible_positions(player)

    # Relievers: score on K rate, ERA, saves+holds (SVHD) — game availability only
    if 'RP' in eligible and 'SP' not in eligible:
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
            era = float(proj.get('era', 0) or proj.get('ERA', 0) or 4.5)
            k = float(proj.get('k', 0) or proj.get('K', 0) or 0)
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


def load_adp_map() -> Dict[str, tuple]:
    """
    Load ADP rankings from CSV.
    Returns {player_name_lower: (adp_rank, position_group)}.
    """
    adp_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'adp.csv')
    adp_map = {}
    try:
        with open(adp_path, newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('Name', '').strip().lower()
                adp = row.get('ADP', '')
                pos = row.get('Position', '').strip().upper()
                if name and adp:
                    adp_map[name] = (float(adp), pos)
    except Exception:
        pass
    return adp_map


def _build_position_adp_scores(adp_map: Dict) -> Dict[str, float]:
    """
    Normalize ADP within each position group so a top-ranked C scores as well
    as a top-ranked 1B even though Cs are scarcer overall.
    Returns {player_name_lower: score_0_to_10}.
    """
    # Group players by position
    by_pos: Dict[str, list] = {}
    for name, (adp, pos) in adp_map.items():
        by_pos.setdefault(pos, []).append((name, adp))

    scores: Dict[str, float] = {}
    for pos, players in by_pos.items():
        players.sort(key=lambda x: x[1])  # best ADP first
        n = len(players)
        for rank, (name, _) in enumerate(players):
            # rank 0 (best) → 10, rank n-1 (worst) → 0
            scores[name] = 10.0 * (1 - rank / max(n - 1, 1))
    return scores


# Cache computed position scores to avoid recomputing every call
_position_adp_cache: Dict[str, float] = {}


def adp_score(player, adp_map: Dict) -> float:
    """
    Return position-normalized ADP score (0–10).
    Falls back to raw ADP score if position info unavailable.
    """
    global _position_adp_cache
    if not _position_adp_cache and adp_map:
        _position_adp_cache = _build_position_adp_scores(adp_map)

    name_lower = player.name.lower()
    if name_lower in _position_adp_cache:
        return _position_adp_cache[name_lower]

    # Fallback: raw ADP score
    entry = adp_map.get(name_lower)
    if entry is None:
        return 0.0
    adp = entry[0] if isinstance(entry, tuple) else entry
    return max(0.0, (300 - adp) / 30)


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
    adp_map = load_adp_map()
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

        eligible = get_eligible_positions(player)
        is_pitcher = any(p in ('SP', 'RP', 'P') for p in eligible)

        if is_pitcher:
            is_confirmed = _normalize_name(player.name) in confirmed_starters
            score = score_pitcher(player, recent_pitching, has_game,
                                  is_confirmed_starter=is_confirmed)
            available_pitchers.append((player, score, has_game))
        else:
            if not has_game:
                no_game.append(player)
                continue
            score = score_hitter(player, recent_hitting, adp_map)
            available_hitters.append((player, score))

    # Sort by score descending
    available_hitters.sort(key=lambda x: x[1], reverse=True)
    available_pitchers.sort(key=lambda x: x[1], reverse=True)

    starters = {}
    used_players = set()

    # ── Optimal hitting slot assignment via recursive backtracking ──────────────
    # With ~15 hitters and 9 slots, the search space is small and fast.
    # Include no-game players scored by ADP as last-resort fillers.
    no_game_scored = [(p, adp_score(p, adp_map)) for p in no_game]
    all_hitter_pool = {id(p): (p, s) for p, s in available_hitters}
    for p, s in no_game_scored:
        all_hitter_pool[id(p)] = (p, s)

    hitting_slots = ['C', '1B', '2B', '3B', 'SS', 'OF', 'OF2', 'OF3', 'UTIL']

    def _assign_slots(slot_idx: int, used_ids: set):
        """Recursive optimal slot assignment. Returns (total_score, {slot: (player, score)})."""
        if slot_idx >= len(hitting_slots):
            return 0.0, {}
        slot = hitting_slots[slot_idx]
        eligible_positions = LINEUP_SLOTS[slot]

        # Option A: leave slot empty
        best_score, best_assign = _assign_slots(slot_idx + 1, used_ids)
        best_score -= 0.001  # tiny penalty for leaving a slot empty

        # Option B: assign a player
        for pid, (player, score) in all_hitter_pool.items():
            if pid in used_ids:
                continue
            if not any(pos in eligible_positions for pos in get_eligible_positions(player)):
                continue
            used_ids.add(pid)
            sub_score, sub_assign = _assign_slots(slot_idx + 1, used_ids)
            used_ids.discard(pid)
            total = score + sub_score
            if total > best_score:
                best_score = total
                best_assign = {slot: (player, score), **sub_assign}

        return best_score, best_assign

    _, optimal_assign = _assign_slots(0, set())

    for slot in hitting_slots:
        if slot in optimal_assign:
            player, score = optimal_assign[slot]
            starters[slot] = (player, score)
            used_players.add(id(player))
            # Note if this player has no game today
            if id(player) in {id(p) for p in no_game}:
                notes.append(f"ℹ️  {player.name} in {slot} (no game today — best available)")
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

    # Everyone not starting goes to bench (including no-game players not placed)
    bench_hitters = [p for p, s in available_hitters if id(p) not in used_players]
    bench_no_game = [p for p in no_game if id(p) not in used_players]
    bench_pitchers = [p for p, s, h in available_pitchers if id(p) not in used_players]

    return {
        'starters': starters,
        'bench': bench_hitters + bench_no_game + bench_pitchers,
        'injured_out': injured,
        'no_game': [p for p in no_game if id(p) not in used_players],
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
