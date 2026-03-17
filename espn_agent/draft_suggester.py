#!/usr/bin/env python3
"""
Draft pick ranking engine.

Scores available players based on:
  1. Base fantasy value (composite projection score)
  2. Positional need bonus (C and SS are scarce)
  3. Category need bonus (if my team is SB-heavy, boost HR hitters)
  4. ADP value bonus (players falling past their ADP)
  5. Round-based penalties (no closers early, no C late)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
from typing import Dict, List, Optional


# Positional scarcity bonuses (added to base score)
POSITION_SCARCITY = {
    'C':  20,   # Only 2 real C options in most drafts
    'SS': 10,   # SS scarcity (Gunnar, Trea, etc.)
    'RP':  8,   # Good closers dry up fast
    '2B':  3,
    '3B':  2,
    '1B':  0,
    'OF':  0,
    'SP':  0,
    'DH': -5,   # DH is flexible but less valuable
}

# Round-based pick strategy
# Before round X, penalize drafting this position
POSITION_ROUND_PENALTIES = {
    'C':   {'before_round': 7,  'penalty': 30},  # Don't draft C before round 7
    'RP':  {'before_round': 9,  'penalty': 20},  # Don't draft closers before round 9
    'SP':  {'before_round': 2,  'penalty': 10},  # Mild SP penalty early (unless elite)
}

# Category names for display
HITTING_CATS  = ['HR', 'RBI', 'R', 'SB', 'AVG']
PITCHING_CATS = ['ERA', 'WHIP', 'K', 'W', 'SV']


def _my_category_strengths(my_roster: List[dict]) -> Dict[str, float]:
    """
    Estimate my team's category strength based on accumulated projections.
    Returns dict: category_name -> projected total (or rate for AVG/ERA/WHIP)
    """
    totals = {'hr': 0, 'rbi': 0, 'r': 0, 'sb': 0,
              'avg_sum': 0, 'avg_count': 0,
              'k': 0, 'w': 0, 'sv': 0,
              'era_sum': 0, 'whip_sum': 0, 'ip_count': 0}

    for p in my_roster:
        if p.get('is_pitcher'):
            totals['k']        += p.get('proj_k', 0) or 0
            totals['w']        += p.get('proj_w', 0) or 0
            totals['sv']       += p.get('proj_sv', 0) or 0
            totals['era_sum']  += p.get('proj_era', 4.5) or 4.5
            totals['whip_sum'] += p.get('proj_whip', 1.3) or 1.3
            totals['ip_count'] += 1
        else:
            totals['hr']        += p.get('proj_hr', 0) or 0
            totals['rbi']       += p.get('proj_rbi', 0) or 0
            totals['r']         += p.get('proj_r', 0) or 0
            totals['sb']        += p.get('proj_sb', 0) or 0
            totals['avg_sum']   += p.get('proj_avg', 0) or 0
            totals['avg_count'] += 1

    strengths = {
        'HR':  totals['hr'],
        'RBI': totals['rbi'],
        'R':   totals['r'],
        'SB':  totals['sb'],
        'AVG': totals['avg_sum'] / max(totals['avg_count'], 1),
        'K':   totals['k'],
        'W':   totals['w'],
        'SV':  totals['sv'],
        'ERA':  totals['era_sum'] / max(totals['ip_count'], 1),
        'WHIP': totals['whip_sum'] / max(totals['ip_count'], 1),
    }
    return strengths


def _positions_filled(my_roster: List[dict]) -> Dict[str, int]:
    """Count how many players I have at each position."""
    counts = {'C': 0, '1B': 0, '2B': 0, '3B': 0, 'SS': 0,
              'OF': 0, 'SP': 0, 'RP': 0, 'DH': 0}
    for p in my_roster:
        for pos in p.get('positions', []):
            if pos in counts:
                counts[pos] += 1
    return counts


def _category_need_bonus(player: dict, strengths: Dict[str, float]) -> float:
    """
    Bonus for filling weak categories, penalty for over-filling strong ones.
    Based on H2H categories: AVG, R, HR, RBI, SB / ERA, WHIP, K, W, SV
    """
    if player.get('is_pitcher'):
        # SP/RP analysis
        bonus = 0.0
        if strengths['K'] < 150:    bonus += (player.get('proj_k', 0) or 0) * 0.05
        if strengths['SV'] < 25:    bonus += (player.get('proj_sv', 0) or 0) * 0.15
        if strengths['ERA'] > 4.0:  bonus += max(0, (5.5 - (player.get('proj_era', 4.5) or 4.5))) * 2
        if strengths['W'] < 60:     bonus += (player.get('proj_w', 0) or 0) * 0.1
        return bonus
    else:
        # Hitter analysis
        bonus = 0.0
        if strengths['HR'] < 100:   bonus += (player.get('proj_hr', 0) or 0) * 0.1
        if strengths['SB'] < 80:    bonus += (player.get('proj_sb', 0) or 0) * 0.15
        if strengths['RBI'] < 300:  bonus += (player.get('proj_rbi', 0) or 0) * 0.03
        if strengths['R'] < 300:    bonus += (player.get('proj_r', 0) or 0) * 0.03
        if strengths['AVG'] < .265: bonus += max(0, ((player.get('proj_avg', 0) or 0) - .240)) * 30
        # Penalty for over-indexing on a category
        if strengths['SB'] > 200:   bonus -= (player.get('proj_sb', 0) or 0) * 0.10
        return bonus


def _positional_need_bonus(player: dict, pos_counts: Dict[str, int]) -> float:
    """Bonus based on filling unfilled roster spots."""
    bonus = 0.0
    # Roster construction targets (for 23-round draft)
    targets = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'SP': 5, 'RP': 2}

    for pos in player.get('positions', []):
        if pos not in targets:
            continue
        if pos_counts.get(pos, 0) < targets[pos]:
            # Need this position
            scarcity = POSITION_SCARCITY.get(pos, 0)
            bonus += max(5, scarcity + 5)

    return bonus


def _adp_value_bonus(player: dict, current_pick: int) -> float:
    """Bonus for players falling past their ADP (value picks)."""
    adp = player.get('adp', 999)
    if adp >= 999:
        return 0.0
    picks_past_adp = current_pick - adp
    if picks_past_adp >= 15:
        return 15.0  # Significant value
    elif picks_past_adp >= 8:
        return 8.0
    elif picks_past_adp >= 3:
        return 3.0
    return 0.0


def _round_penalty(player: dict, round_num: int) -> float:
    """Penalty for drafting certain positions too early or too late."""
    penalty = 0.0
    positions = player.get('positions', [])

    for pos, rule in POSITION_ROUND_PENALTIES.items():
        if pos in positions and round_num < rule['before_round']:
            # Apply penalty but reduce for elite players
            base_score = player.get('fantasy_score', 0)
            if base_score < 60:  # Not elite enough to override strategy
                penalty += rule['penalty']
    return penalty


def score_player(player: dict, my_roster: List[dict], round_num: int,
                 current_pick: int, strengths: Dict[str, float],
                 pos_counts: Dict[str, int]) -> float:
    """
    Full composite score for a single available player.
    Higher score = better pick for this team right now.
    """
    base = player.get('fantasy_score', 0) * 1.5
    pos_bonus = _positional_need_bonus(player, pos_counts)
    cat_bonus = _category_need_bonus(player, strengths)
    adp_bonus = _adp_value_bonus(player, current_pick)
    penalty   = _round_penalty(player, round_num)

    return base + pos_bonus + cat_bonus + adp_bonus - penalty


def _build_reason(player: dict, my_roster: List[dict], round_num: int,
                  current_pick: int, pos_counts: Dict[str, int],
                  strengths: Dict[str, float]) -> str:
    """Generate a one-line reason for why this player is ranked here."""
    reasons = []
    positions = player.get('positions', [])
    name = player.get('name', '')

    # ADP value
    adp = player.get('adp', 999)
    picks_past = current_pick - adp
    if picks_past >= 8:
        reasons.append(f"falling {int(picks_past)} picks past ADP (value!)")

    # Positional need
    targets = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'SP': 5, 'RP': 2}
    for pos in positions:
        if pos in targets and pos_counts.get(pos, 0) < targets[pos]:
            reasons.append(f"fills your {pos} need")
            break

    # Category strength
    if player.get('is_pitcher'):
        if (player.get('proj_sv', 0) or 0) >= 25 and strengths['SV'] < 30:
            reasons.append("top closer, SV scarce on your team")
        elif (player.get('proj_k', 0) or 0) >= 200:
            reasons.append(f"{player['proj_k']} projected K — elite strikeouts")
        elif (player.get('proj_era', 9) or 9) < 3.20:
            reasons.append(f"{player['proj_era']:.2f} ERA anchor")
    else:
        sb = player.get('proj_sb', 0) or 0
        hr = player.get('proj_hr', 0) or 0
        avg = player.get('proj_avg', 0) or 0
        if sb >= 30 and strengths['SB'] < 100:
            reasons.append(f"SB machine ({sb} proj SB), your team needs speed")
        elif hr >= 35:
            reasons.append(f"elite power ({hr} proj HR)")
        elif avg >= .300:
            reasons.append(f"AVG anchor ({avg:.3f} proj)")

    if not reasons:
        reasons.append("best available value at this pick")

    return '; '.join(reasons[:2])


def get_top_picks(available: Dict[str, dict], my_roster: List[dict],
                  round_num: int, current_pick: int, n: int = 10) -> List[dict]:
    """
    Return top N picks ranked for right now.

    Returns list of player dicts enriched with:
        score, reason, adp_delta
    """
    if not available:
        return []

    strengths = _my_category_strengths(my_roster)
    pos_counts = _positions_filled(my_roster)

    scored = []
    for lname, player in available.items():
        s = score_player(player, my_roster, round_num, current_pick, strengths, pos_counts)
        p = dict(player)
        p['score'] = s
        p['adp_delta'] = current_pick - player.get('adp', current_pick)
        p['reason'] = _build_reason(player, my_roster, round_num, current_pick, pos_counts, strengths)
        scored.append(p)

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:n]


def format_my_turn_display(top_picks: List[dict], my_roster: List[dict],
                           round_num: int, pick_num: int,
                           total_picks: int, team_name: str) -> str:
    """
    Format the "YOUR TURN" terminal display block.
    """
    lines = []
    lines.append("")
    lines.append("═" * 52)
    lines.append(f"  YOUR PICK — Round {round_num}, Pick {pick_num} of {total_picks}")
    lines.append("═" * 52)

    # My current roster summary
    if my_roster:
        roster_str = "  ".join(
            f"{p['name'].split()[-1]}({'/'.join(p['positions'][:2])})"
            for p in my_roster[:8]
        )
        lines.append(f"MY TEAM: {roster_str}")

    # Positional needs
    pos_counts = _positions_filled(my_roster)
    targets = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'SP': 5, 'RP': 2}
    needs = [pos for pos, target in targets.items() if pos_counts.get(pos, 0) < target]

    # Category weaknesses
    strengths = _my_category_strengths(my_roster)
    weak_cats = []
    if strengths['HR'] < 80:   weak_cats.append('HR')
    if strengths['SB'] < 60:   weak_cats.append('SB')
    if strengths['RBI'] < 200: weak_cats.append('RBI')
    if strengths['SV'] < 20:   weak_cats.append('SV')
    if strengths['K'] < 120:   weak_cats.append('K')
    if strengths['AVG'] < .260 and len(my_roster) > 3: weak_cats.append('AVG')

    if needs or weak_cats:
        needs_str = ', '.join(needs) if needs else 'all filled'
        cats_str  = ', '.join(weak_cats) if weak_cats else 'balanced'
        lines.append(f"NEEDS: {needs_str}  |  Weak cats: {cats_str}")

    lines.append("")
    lines.append("TOP PICKS AVAILABLE:")

    for i, p in enumerate(top_picks[:6], start=1):
        pos_str = '/'.join(p['positions'][:2])
        adp     = p.get('adp', 999)
        adp_str = f"ADP:{adp:.0f}" if adp < 900 else "ADP:NR"
        delta   = p.get('adp_delta', 0)
        value   = f"  ↑ falling!" if delta >= 8 else ""

        lines.append(f"  {i}. {p['name']:<25} {pos_str:<8} {adp_str}{value}")

        # Stats line
        stats_parts = []
        if not p.get('is_pitcher'):
            if p.get('proj_avg'):  stats_parts.append(f".{int(p['proj_avg']*1000):03d}")
            if p.get('proj_hr'):   stats_parts.append(f"{p['proj_hr']} HR")
            if p.get('proj_rbi'):  stats_parts.append(f"{p['proj_rbi']} RBI")
            if p.get('proj_r'):    stats_parts.append(f"{p['proj_r']} R")
            if p.get('proj_sb'):   stats_parts.append(f"{p['proj_sb']} SB")
        else:
            if p.get('proj_era'):  stats_parts.append(f"{p['proj_era']:.2f} ERA")
            if p.get('proj_whip'): stats_parts.append(f"{p['proj_whip']:.2f} WHIP")
            if p.get('proj_k'):    stats_parts.append(f"{p['proj_k']} K")
            if p.get('proj_w'):    stats_parts.append(f"{p['proj_w']} W")
            if p.get('proj_sv'):   stats_parts.append(f"{p['proj_sv']} SV")
        lines.append(f"       {', '.join(stats_parts)}")

    return '\n'.join(lines)


def get_claude_pick_advice(top_picks: List[dict], my_roster: List[dict],
                           round_num: int, team_name: str) -> str:
    """
    Ask Claude for a single-sentence draft recommendation.
    """
    try:
        from espn_agent.agent import _call_claude
    except ImportError:
        return "[Claude unavailable]"

    if not top_picks:
        return "[No picks to analyze]"

    # Build concise prompt
    top3 = top_picks[:3]
    picks_text = '\n'.join(
        f"  {i+1}. {p['name']} ({'/'.join(p['positions'][:2])}) — {p.get('reason', '')}"
        for i, p in enumerate(top3)
    )

    my_pos = ', '.join(
        f"{p['name'].split()[-1]}({'/'.join(p['positions'][:1])})"
        for p in my_roster[-5:]
    ) if my_roster else 'none yet'

    prompt = (
        f"You are a fantasy baseball draft expert. It's round {round_num} for team \"{team_name}\".\n"
        f"My recent picks: {my_pos}\n"
        f"Top options:\n{picks_text}\n\n"
        f"Give exactly ONE sentence (max 20 words) saying which to pick and why. Start with the player's last name."
    )
    return _call_claude(prompt, max_tokens=80)


if __name__ == '__main__':
    # Quick test with fallback data
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from espn_agent.draft_data import build_player_pool, get_available

    pool = build_player_pool()
    available = get_available(pool)
    my_roster = []

    top = get_top_picks(available, my_roster, round_num=1, current_pick=5)
    display = format_my_turn_display(top, my_roster, round_num=1, pick_num=5,
                                     total_picks=230, team_name='Sandy Koufax')
    print(display)
    print(f"\nTop pick: {top[0]['name']} — {top[0]['reason']}")
