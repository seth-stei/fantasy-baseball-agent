#!/usr/bin/env python3
"""
Pre-draft data loader.
Builds a unified player pool with projections and ADP rankings.

Auto-fetches 2025 Fangraphs stats via pybaseball — no manual CSV downloads needed.
Optionally uses data/adp.csv if you drop one in before draft day (FantasyPros format).
"""
import os
import sys
import csv
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
from typing import Dict, List, Optional

LEAGUE_ID = int(os.getenv('ESPN_LEAGUE_ID', '1007942881'))
YEAR = int(os.getenv('ESPN_YEAR', '2026'))
ESPN_S2 = os.getenv('ESPN_S2', '')
ESPN_SWID = os.getenv('ESPN_SWID', '')

# ESPN defaultPositionId → position string
ESPN_DEFAULT_POS = {
    1: 'SP', 2: 'C', 3: '1B', 4: '2B', 5: '3B',
    6: 'SS', 7: 'OF', 8: 'OF', 9: 'OF', 10: 'DH', 11: 'RP',
}

# ESPN lineup slot ID → position string (for eligibleSlots)
SLOT_TO_POS = {
    0: 'C', 1: '1B', 2: '2B', 3: '3B', 4: 'SS',
    5: 'OF', 11: 'SP', 12: 'RP',
}


def fetch_espn_player_map() -> Dict[int, dict]:
    """
    Fetch all players from ESPN with name, position, ESPN ID.
    Returns dict: espn_player_id -> {name, positions, espn_id}
    Also returns a secondary dict: lower_name -> espn_player_id
    """
    print("  Fetching ESPN player roster (names + positions)...")
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{YEAR}/players"
    params = {'scoringPeriodId': '0', 'view': 'players_wl'}
    cookies = {'espn_s2': ESPN_S2, 'SWID': ESPN_SWID}

    id_map = {}     # espn_id -> player dict
    name_map = {}   # lower_name -> espn_id
    try:
        resp = requests.get(url, params=params, cookies=cookies, timeout=30)
        resp.raise_for_status()
        players = resp.json()

        for p in players:
            pid = p.get('id')
            full_name = p.get('fullName', '').strip()
            if not full_name or not pid:
                continue

            # Primary position
            default_pos_id = p.get('defaultPositionId', 0)
            primary_pos = ESPN_DEFAULT_POS.get(default_pos_id, 'UTIL')

            # All eligible positions from eligible slots
            positions = []
            for slot_id in p.get('eligibleSlots', []):
                pos = SLOT_TO_POS.get(slot_id)
                if pos and pos not in positions:
                    positions.append(pos)
            if not positions:
                positions = [primary_pos]

            id_map[pid] = {
                'name': full_name,
                'positions': positions,
                'espn_id': pid,
            }
            name_map[full_name.lower()] = pid

        print(f"  Got {len(id_map)} players from ESPN")
    except Exception as e:
        print(f"  Warning: ESPN player list unavailable ({e})")

    return id_map, name_map


def fetch_fielding_positions() -> Dict[str, List[str]]:
    """
    Fetch 2025 Fangraphs fielding stats to get primary positions.
    Returns dict: lower_name -> [positions]
    CF/LF/RF all map to OF.
    """
    print("  Fetching 2025 fielding data (positions)...")
    fg_to_pos = {
        'C': 'C', '1B': '1B', '2B': '2B', '3B': '3B', 'SS': 'SS',
        'LF': 'OF', 'CF': 'OF', 'RF': 'OF', 'OF': 'OF', 'DH': 'DH',
        'P': 'SP',  # edge case
    }
    pos_map: Dict[str, List[str]] = {}
    try:
        import pybaseball
        pybaseball.cache.enable()
        df = pybaseball.fielding_stats(2025, qual=1)
        for _, row in df.iterrows():
            name = str(row.get('Name', '')).strip()
            pos_raw = str(row.get('Pos', '')).strip()
            mapped = fg_to_pos.get(pos_raw.upper())
            if name and mapped:
                lname = name.lower()
                if lname not in pos_map:
                    pos_map[lname] = [mapped]
                elif mapped not in pos_map[lname]:
                    pos_map[lname].append(mapped)
        print(f"  Got positions for {len(pos_map)} fielders")
    except Exception as e:
        print(f"  Warning: fielding positions unavailable ({e})")
    return pos_map


def fetch_fangraphs_batting() -> Dict[str, dict]:
    """
    Fetch 2025 Fangraphs batting stats via pybaseball.
    Returns dict: lower_name -> batting stats
    """
    print("  Fetching 2025 batting projections (Fangraphs)...")
    try:
        import pybaseball
        pybaseball.cache.enable()
        df = pybaseball.batting_stats(2025, qual=200)
        stats = {}
        for _, row in df.iterrows():
            name = str(row.get('Name', '')).strip()
            if not name:
                continue
            hr      = int(row.get('HR', 0) or 0)
            doubles = int(row.get('2B', 0) or 0)
            triples = int(row.get('3B', 0) or 0)
            stats[name.lower()] = {
                'name': name,
                'avg': float(row.get('AVG', 0) or 0),
                'hr': hr,
                'rbi': int(row.get('RBI', 0) or 0),
                'r': int(row.get('R', 0) or 0),
                'sb': int(row.get('SB', 0) or 0),
                'pa': int(row.get('PA', 0) or 0),
                'xbh': doubles + triples + hr,  # extra base hits (actual league category)
            }
        print(f"  Got {len(stats)} hitters from Fangraphs")
        return stats
    except Exception as e:
        print(f"  Warning: Fangraphs batting unavailable ({e}), using fallback")
        return {}


def fetch_fangraphs_pitching() -> Dict[str, dict]:
    """
    Fetch 2025 Fangraphs pitching stats via pybaseball.
    Returns dict: lower_name -> pitching stats
    """
    print("  Fetching 2025 pitching projections (Fangraphs)...")
    try:
        import pybaseball
        pybaseball.cache.enable()
        df = pybaseball.pitching_stats(2025, qual=20)
        stats = {}
        for _, row in df.iterrows():
            name = str(row.get('Name', '')).strip()
            if not name:
                continue
            gs   = int(row.get('GS', 0) or 0)
            sv   = int(row.get('SV', 0) or 0)
            holds = int(row.get('HLD', 0) or row.get('HD', 0) or 0)
            era  = float(row.get('ERA', 99) or 99)
            # Fangraphs pitching_stats has no QS column — compute from GS + ERA.
            # QS rate: ~80% for sub-3 ERA, ~65% at 3.5, ~50% at 4.5, min 15%.
            qs_rate = max(0.15, min(0.80, 0.80 - max(0.0, era - 3.0) * 0.10))
            qs   = int(gs * qs_rate) if gs >= 5 else 0
            ip   = float(row.get('IP', 0) or 0)
            whip = float(row.get('WHIP', 99) or 99)
            # Fangraphs uses 'SO' for strikeouts in pitching
            k = int(row.get('SO', 0) or row.get('K', 0) or 0)
            w = int(row.get('W', 0) or 0)
            stats[name.lower()] = {
                'name': name,
                'era': era,
                'whip': whip,
                'k': k,
                'w': w,
                'sv': sv,
                'holds': holds,
                'qs': qs,
                'svhd': sv + holds,  # actual league scoring category
                'ip': ip,
                'gs': gs,
                'is_closer': sv >= 15,
                'is_starter': gs >= 10,
            }
        print(f"  Got {len(stats)} pitchers from Fangraphs")
        return stats
    except Exception as e:
        print(f"  Warning: Fangraphs pitching unavailable ({e}), using fallback")
        return {}


def fetch_adp_from_fantasypros(force_refresh: bool = False) -> bool:
    """
    Auto-download 2026 H2H consensus rankings from FantasyPros and save to data/adp.csv.
    Returns True if successful.
    Only re-fetches if the file is missing or force_refresh=True.
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, 'data', 'adp.csv')
    if os.path.exists(path) and not force_refresh:
        return True  # Already have it

    print("  Fetching 2026 consensus rankings from FantasyPros...")
    url = ('https://partners.fantasypros.com/api/v1/consensus-rankings.php'
           '?sport=MLB&year=2026&week=0&position=ALL&type=RK&scoring=H2H')
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        resp.raise_for_status()
        players = resp.json().get('players', [])
        if not players:
            return False
        os.makedirs(os.path.join(base, 'data'), exist_ok=True)
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['Name', 'ADP', 'Position'])
            writer.writeheader()
            for p in players:
                writer.writerow({
                    'Name': p.get('player_name', ''),
                    'ADP': p.get('rank_ecr', 999),
                    'Position': p.get('primary_position', ''),
                })
        print(f"  Saved {len(players)} players to data/adp.csv")
        return True
    except Exception as e:
        print(f"  Warning: could not fetch FantasyPros ADP: {e}")
        return False


def load_adp_csv() -> Dict[str, float]:
    """
    Load optional manual ADP override (data/adp.csv).
    Expected columns: Name, ADP (or Rank). Case-insensitive header detection.
    Returns dict: lower_name -> adp_rank
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, 'data', 'adp.csv')
    if not os.path.exists(path):
        return {}
    adp_map = {}
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                name = (row.get('Name') or row.get('Player') or row.get('name') or '').strip()
                adp_raw = (row.get('ADP') or row.get('Rank') or row.get('Overall') or str(i + 1)).strip()
                try:
                    adp_map[name.lower()] = float(adp_raw)
                except (ValueError, TypeError):
                    adp_map[name.lower()] = float(i + 1)
        print(f"  Loaded ADP overrides for {len(adp_map)} players from data/adp.csv")
    except Exception as e:
        print(f"  Warning: could not load data/adp.csv: {e}")
    return adp_map


def _regress(val: float, mean: float, pct: float = 0.35) -> float:
    """Pull an extreme value pct% toward a sustainable mean (scoring only, not display)."""
    return val + (mean - val) * pct


def compute_hitter_score(stats: dict) -> float:
    """
    Composite fantasy score for hitters (H2H: AVG, R, HR, XBH, RBI, SB).
    Applies regression toward mean for outlier 2025 seasons so extreme single-year
    performances don't dominate rankings vs. the real draft market.
    """
    # Regress extreme raw stats toward realistic season-long expectations
    hr  = stats.get('hr', 0)
    if hr > 45:
        hr = 45 + (hr - 45) * 0.5   # 60 HR → 52, 50 HR → 47
    rbi = min(stats.get('rbi', 0), 125)
    r   = min(stats.get('r', 0),   120)
    sb  = min(stats.get('sb', 0),  60)
    xbh = stats.get('xbh', 0) or int(stats.get('hr', 0) * 1.6)
    xbh = min(xbh, 85)
    avg = stats.get('avg', .250)
    if avg > .320:
        avg = _regress(avg, .320, 0.40)  # .360 → .336

    hr_score  = min(hr  / 45.0, 1.0) * 20
    rbi_score = min(rbi / 120.0, 1.0) * 18
    r_score   = min(r   / 115.0, 1.0) * 15
    sb_score  = min(sb  / 50.0,  1.0) * 15
    xbh_score = min(xbh / 70.0,  1.0) * 12
    avg_score = max(0.0, (avg - .220) / (.330 - .220)) * 20
    return hr_score + rbi_score + r_score + sb_score + xbh_score + avg_score


def compute_pitcher_score(stats: dict) -> float:
    """
    Composite fantasy score for pitchers (H2H: ERA, WHIP, K, W, QS, SVHD).
    Applies regression toward mean for outlier ERA/WHIP seasons — a pitcher
    who posted 1.73 ERA in 2025 will not repeat that; treat it as ~2.20 for ranking.
    """
    k_score  = min(stats.get('k', 0)  / 250.0, 1.0) * 22
    w_score  = min(stats.get('w', 0)  / 20.0,  1.0) * 15
    qs_score = min(stats.get('qs', 0) / 25.0,  1.0) * 15
    svhd = stats.get('svhd', 0) or stats.get('sv', 0) or 0
    svhd_score = min(svhd / 45.0, 1.0) * 13

    # Regress extreme ERA toward a sustainable floor (2.80 for scoring purposes)
    era = stats.get('era', 4.50)
    if era < 2.80:
        era = _regress(era, 2.80, 0.40)   # 1.73 → 2.16, 2.21 → 2.45
    era_score = max(0.0, (5.50 - era) / (5.50 - 2.00)) * 18

    # Regress extreme WHIP similarly
    whip = stats.get('whip', 1.30)
    if whip < 0.90:
        whip = _regress(whip, 0.90, 0.35)  # 0.70 → 0.77
    whip_score = max(0.0, (1.80 - whip) / (1.80 - 0.80)) * 17

    return k_score + w_score + qs_score + svhd_score + era_score + whip_score


# Fallback player pool — used when Fangraphs is unavailable (e.g. --mock mode)
FALLBACK_PLAYERS = [
    # (name, positions, adp, hr, rbi, r, sb, avg, era, whip, k, w, sv)
    ('Corbin Carroll',      ['OF'],     1,   25, 75,  95, 35, .280, None, None, None, None, None),
    ('Ronald Acuna Jr.',    ['OF'],     2,   30, 85, 100, 60, .290, None, None, None, None, None),
    ('Gunnar Henderson',    ['SS','3B'],3,   35, 95,  95, 15, .275, None, None, None, None, None),
    ('Freddie Freeman',     ['1B'],     4,   25, 95,  95,  5, .310, None, None, None, None, None),
    ('Juan Soto',           ['OF'],     5,   30, 90,  95, 10, .290, None, None, None, None, None),
    ('Yordan Alvarez',      ['OF','DH'],6,   35, 105, 95,  0, .295, None, None, None, None, None),
    ('Fernando Tatis Jr.',  ['SS','OF'],7,   25, 85,  90, 25, .275, None, None, None, None, None),
    ('Trea Turner',         ['SS'],     8,   20, 80,  95, 30, .285, None, None, None, None, None),
    ('Paul Goldschmidt',    ['1B'],     9,   25, 95,  90,  5, .285, None, None, None, None, None),
    ('Mookie Betts',        ['OF','2B'],10,  30, 90,  95, 15, .285, None, None, None, None, None),
    ('Bobby Witt Jr.',      ['SS'],     11,  25, 85,  95, 35, .285, None, None, None, None, None),
    ('Julio Rodriguez',     ['OF'],     12,  25, 80,  90, 35, .275, None, None, None, None, None),
    ('Jose Ramirez',        ['3B'],     13,  25, 90,  90, 25, .275, None, None, None, None, None),
    ('Adolis Garcia',       ['OF'],     14,  30, 95,  85,  10,.265, None, None, None, None, None),
    ('Elly De La Cruz',     ['SS','3B'],15,  25, 80,  90, 50, .260, None, None, None, None, None),
    ('Matt Olson',          ['1B'],     16,  35, 105, 85,  0, .270, None, None, None, None, None),
    ('Kyle Tucker',         ['OF'],     17,  30, 95,  90, 20, .285, None, None, None, None, None),
    ('William Contreras',   ['C'],      18,  20, 75,  75,  5, .280, None, None, None, None, None),
    ('Adley Rutschman',     ['C'],      19,  20, 70,  75,  5, .275, None, None, None, None, None),
    ('Marcus Semien',       ['2B'],     20,  25, 85,  95, 15, .275, None, None, None, None, None),
    ('Rafael Devers',       ['3B'],     21,  30, 100, 85,  5, .270, None, None, None, None, None),
    ('Nolan Arenado',       ['3B'],     22,  25, 90,  80,  2, .270, None, None, None, None, None),
    ('Alex Bregman',        ['3B','2B'],23,  20, 85,  85,  5, .270, None, None, None, None, None),
    ('Spencer Strider',     ['SP'],     24, None,None,None,None,None, 3.20, 1.00, 250, 18, None),
    ('Gerrit Cole',         ['SP'],     25, None,None,None,None,None, 3.00, 0.95, 245, 18, None),
    ('Sandy Alcantara',     ['SP'],     26, None,None,None,None,None, 2.90, 1.00, 200, 14, None),
    ('Zack Wheeler',        ['SP'],     27, None,None,None,None,None, 3.10, 1.05, 230, 15, None),
    ('Justin Verlander',    ['SP'],     28, None,None,None,None,None, 3.40, 1.05, 180, 14, None),
    ('Logan Webb',          ['SP'],     29, None,None,None,None,None, 3.25, 1.10, 185, 13, None),
    ('Framber Valdez',      ['SP'],     30, None,None,None,None,None, 3.00, 1.10, 185, 14, None),
    ('Shohei Ohtani',       ['DH'],     31,  45, 110, 105, 25, .300, None, None, None, None, None),
    ('Michael Harris II',   ['OF'],     32,  20, 70,  85, 30, .270, None, None, None, None, None),
    ('Luis Robert Jr.',     ['OF'],     33,  25, 75,  85, 20, .270, None, None, None, None, None),
    ('Ryan McMahon',        ['3B','2B'],34,  20, 80,  80, 10, .265, None, None, None, None, None),
    ('Wander Franco',       ['SS'],     35,  10, 65,  75, 15, .285, None, None, None, None, None),
    ('Dansby Swanson',      ['SS'],     36,  20, 75,  80, 15, .250, None, None, None, None, None),
    ('Jeremy Pena',         ['SS'],     37,  15, 65,  75, 10, .255, None, None, None, None, None),
    ('Brice Turang',        ['2B','SS'],38,   5, 45,  70, 30, .255, None, None, None, None, None),
    ('Ozzie Albies',        ['2B'],     39,  20, 80,  85, 15, .260, None, None, None, None, None),
    ('Jeff McNeil',         ['2B','OF'],40,  10, 65,  70,  5, .295, None, None, None, None, None),
    ('Andres Gimenez',      ['2B'],     41,  10, 65,  75, 15, .260, None, None, None, None, None),
    ('Luis Arraez',         ['2B','1B'],42,   5, 60,  70,  5, .320, None, None, None, None, None),
    ('Max Muncy',           ['1B','2B','3B'],43,25,85,80, 5, .235, None, None, None, None, None),
    ('Christian Encarnacion-Strand',['1B'],44,25,85,75, 5,.270, None, None, None, None, None),
    ('Cal Raleigh',         ['C'],      45,  30, 80,  70,  2, .235, None, None, None, None, None),
    ('Gabriel Moreno',      ['C'],      46,  10, 55,  65,  5, .270, None, None, None, None, None),
    ('MJ Melendez',         ['C','OF'], 47,  15, 60,  65,  5, .245, None, None, None, None, None),
    ('Alejandro Kirk',      ['C'],      48,  10, 55,  60,  0, .275, None, None, None, None, None),
    ('Will Smith',          ['C'],      49,  15, 65,  65,  2, .270, None, None, None, None, None),
    ('Sean Murphy',         ['C'],      50,  18, 65,  65,  2, .245, None, None, None, None, None),
    ('Jose Altuve',         ['2B'],     51,  15, 65,  85, 15, .280, None, None, None, None, None),
    ('Xander Bogaerts',     ['SS'],     52,  18, 75,  80,  5, .265, None, None, None, None, None),
    ('Corey Seager',        ['SS'],     53,  30, 95,  85,  5, .285, None, None, None, None, None),
    ('Cody Bellinger',      ['OF','1B'],54,  25, 85,  85, 15, .270, None, None, None, None, None),
    ('Christian Yelich',    ['OF'],     55,  20, 75,  85, 20, .275, None, None, None, None, None),
    ('Teoscar Hernandez',   ['OF'],     56,  25, 85,  80, 10, .265, None, None, None, None, None),
    ('George Springer',     ['OF','2B'],57,  25, 80,  85, 15, .265, None, None, None, None, None),
    ('Starling Marte',      ['OF'],     58,  10, 60,  80, 30, .270, None, None, None, None, None),
    ('Tommy Edman',         ['2B','SS','OF'],59,10,55,80,25,.270, None, None, None, None, None),
    ('Nolan Gorman',        ['2B'],     60,  30, 85,  80,  5, .250, None, None, None, None, None),
    ('Vladimir Guerrero Jr.',['1B'],    61,  30, 95,  90,  2, .290, None, None, None, None, None),
    ('Josh Jung',           ['3B'],     62,  25, 85,  80,  5, .265, None, None, None, None, None),
    ('Bryce Harper',        ['1B'],     63,  30, 95,  95, 10, .300, None, None, None, None, None),
    ('Pete Alonso',         ['1B'],     64,  40, 120, 90,  2, .260, None, None, None, None, None),
    ('Nathaniel Lowe',      ['1B'],     65,  20, 85,  85,  5, .270, None, None, None, None, None),
    ('Ryan Mountcastle',    ['1B'],     66,  25, 85,  80,  2, .265, None, None, None, None, None),
    ('Josh Naylor',         ['1B'],     67,  25, 90,  80,  2, .265, None, None, None, None, None),
    ('Ty France',           ['1B'],     68,  15, 70,  70,  2, .275, None, None, None, None, None),
    ('Jordan Walker',       ['OF','3B'],69,  20, 75,  80, 10, .270, None, None, None, None, None),
    ('Jackson Holliday',    ['SS','2B'],70,  15, 65,  75, 15, .265, None, None, None, None, None),
    ('Dylan Cease',         ['SP'],     71, None,None,None,None,None, 3.40, 1.10, 220, 14, None),
    ('Kevin Gausman',       ['SP'],     72, None,None,None,None,None, 3.20, 1.05, 210, 14, None),
    ('Robbie Ray',          ['SP'],     73, None,None,None,None,None, 3.50, 1.10, 215, 13, None),
    ('Max Fried',           ['SP'],     74, None,None,None,None,None, 3.30, 1.05, 185, 14, None),
    ('Pablo Lopez',         ['SP'],     75, None,None,None,None,None, 3.40, 1.10, 195, 13, None),
    ('Hunter Brown',        ['SP'],     76, None,None,None,None,None, 3.50, 1.10, 190, 12, None),
    ('Cristian Javier',     ['SP'],     77, None,None,None,None,None, 3.60, 1.10, 200, 12, None),
    ('Alexis Diaz',         ['RP'],     78, None,None,None,None,None, 2.80, 1.00,  80,  4, 35),
    ('Edwin Diaz',          ['RP'],     79, None,None,None,None,None, 2.60, 0.90,  90,  3, 35),
    ('Ryan Helsley',        ['RP'],     80, None,None,None,None,None, 2.50, 0.90,  85,  4, 38),
    ('Josh Hader',          ['RP'],     81, None,None,None,None,None, 2.80, 1.00,  90,  3, 40),
    ('Emmanuel Clase',      ['RP'],     82, None,None,None,None,None, 2.40, 0.95,  70,  3, 42),
    ('Felix Bautista',      ['RP'],     83, None,None,None,None,None, 2.60, 0.95,  95,  3, 36),
    ('Jordan Romano',       ['RP'],     84, None,None,None,None,None, 2.80, 1.00,  80,  3, 35),
    ('Clay Holmes',         ['RP'],     85, None,None,None,None,None, 2.90, 1.05,  75,  4, 30),
    ('Camilo Doval',        ['RP'],     86, None,None,None,None,None, 3.00, 1.05,  80,  4, 28),
    ('Devin Williams',      ['RP'],     87, None,None,None,None,None, 2.70, 0.95,  90,  4, 30),
    ('Carlos Rodon',        ['SP'],     88, None,None,None,None,None, 3.60, 1.10, 200, 12, None),
    ('Shane McClanahan',    ['SP'],     89, None,None,None,None,None, 3.20, 1.05, 220, 14, None),
    ('Kodai Senga',         ['SP'],     90, None,None,None,None,None, 3.10, 1.00, 220, 14, None),
    ('George Kirby',        ['SP'],     91, None,None,None,None,None, 3.30, 0.95, 190, 13, None),
    ('Bailey Ober',         ['SP'],     92, None,None,None,None,None, 3.50, 1.05, 185, 12, None),
    ('Taj Bradley',         ['SP'],     93, None,None,None,None,None, 3.60, 1.10, 180, 11, None),
    ('Michael Kopech',      ['SP'],     94, None,None,None,None,None, 3.50, 1.10, 190, 11, None),
    ('Tyler Glasnow',       ['SP'],     95, None,None,None,None,None, 3.00, 1.00, 230, 14, None),
    ('Aaron Nola',          ['SP'],     96, None,None,None,None,None, 3.50, 1.05, 210, 13, None),
    ('Joe Ryan',            ['SP'],     97, None,None,None,None,None, 3.60, 1.05, 195, 12, None),
    ('Josiah Gray',         ['SP'],     98, None,None,None,None,None, 3.70, 1.10, 185, 12, None),
    ('Triston McKenzie',    ['SP'],     99, None,None,None,None,None, 3.50, 1.05, 200, 12, None),
    ('Ian Gibaut',          ['RP'],    100, None,None,None,None,None, 3.00, 1.05,  70,  4, 25),
]


def _build_pool_from_fallback() -> Dict[str, dict]:
    """Build player pool from the hardcoded fallback list."""
    pool = {}
    for entry in FALLBACK_PLAYERS:
        name, positions, adp, hr, rbi, r, sb, avg, era, whip, k, w, sv = entry
        lname = name.lower()
        is_pitcher = era is not None
        is_closer = (sv or 0) >= 15

        # Compute proxy values for actual league categories not in the fallback tuple
        proj_xbh   = int((hr or 0) * 1.6) if not is_pitcher else None
        # QS proxy: ~75% of wins come from quality starts for SPs (not closers)
        proj_qs    = int((w or 0) * 0.75) if is_pitcher and not is_closer else None
        proj_holds = 0  # simplified; real holds data only comes from Fangraphs

        pool[lname] = {
            'name': name,
            'positions': positions,
            'adp': adp,
            'fantasy_score': compute_pitcher_score({
                'k': k or 0, 'w': w or 0, 'sv': sv or 0, 'qs': proj_qs or 0,
                'era': era or 4.5, 'whip': whip or 1.3
            }) if is_pitcher else compute_hitter_score({
                'hr': hr or 0, 'rbi': rbi or 0, 'r': r or 0,
                'sb': sb or 0, 'avg': avg or .250, 'xbh': proj_xbh or 0
            }),
            'proj_avg': avg, 'proj_hr': hr, 'proj_rbi': rbi,
            'proj_r': r, 'proj_sb': sb, 'proj_xbh': proj_xbh,
            'proj_era': era, 'proj_whip': whip, 'proj_k': k,
            'proj_w': w, 'proj_sv': sv, 'proj_holds': proj_holds, 'proj_qs': proj_qs,
            'is_pitcher': is_pitcher,
            'is_closer': is_closer,
            'drafted_by': None,
            'pick_number': None,
            'round': None,
        }
    return pool


def build_player_pool() -> Dict[str, dict]:
    """
    Build the master player pool for draft day.

    Auto-fetches everything — no manual steps required.
    Optionally loads data/adp.csv if it exists.

    Returns dict: lower_player_name -> player_dict
    """
    print("\nLoading draft data (this takes ~30 seconds)...")

    # Auto-fetch FantasyPros ADP if data/adp.csv is missing
    fetch_adp_from_fantasypros()

    espn_id_map, espn_name_map = fetch_espn_player_map()
    fielding_positions = fetch_fielding_positions()   # name -> [pos] from Fangraphs
    batting_stats = fetch_fangraphs_batting()
    pitching_stats = fetch_fangraphs_pitching()
    adp_override = load_adp_csv()

    # If both Fangraphs fetches failed, use hardcoded fallback
    if not batting_stats and not pitching_stats:
        print("  Using built-in fallback player list")
        pool = _build_pool_from_fallback()
        if adp_override:
            for lname in pool:
                if lname in adp_override:
                    pool[lname]['adp'] = adp_override[lname]
        print(f"\nPlayer pool ready: {len(pool)} players (fallback mode)")
        return pool

    pool = {}

    # Score all hitters and pitchers, then rank them together.
    # Both scoring functions top out ~80-85 for elite players, so direct
    # comparison is reasonable. This gives pitchers realistic ADP positions
    # (top ace ~pick 20-30) without needing a manual ADP file.
    hitter_scored = [(lname, s, compute_hitter_score(s), False)
                     for lname, s in batting_stats.items()]
    pitcher_scored = [(lname, s, compute_pitcher_score(s), True)
                      for lname, s in pitching_stats.items()]

    all_scored = hitter_scored + pitcher_scored
    all_scored.sort(key=lambda x: x[2], reverse=True)

    for unified_rank, (lname, stats, score, is_pitcher) in enumerate(all_scored, start=1):
        if is_pitcher:
            espn_pid = espn_name_map.get(lname)
            espn_pos = espn_id_map.get(espn_pid, {}).get('positions') if espn_pid else None
            if espn_pos:
                positions = espn_pos
            else:
                positions = ['SP'] if stats.get('is_starter') else ['RP']

            adp = adp_override.get(lname, float(unified_rank))
            pool[lname] = {
                'name': stats['name'],
                'positions': positions,
                'adp': adp,
                'fantasy_score': score,
                'proj_avg': None, 'proj_hr': None, 'proj_rbi': None,
                'proj_r': None, 'proj_sb': None, 'proj_xbh': None,
                'proj_era': stats.get('era'), 'proj_whip': stats.get('whip'),
                'proj_k': stats.get('k'), 'proj_w': stats.get('w'),
                'proj_sv': stats.get('sv'), 'proj_holds': stats.get('holds'),
                'proj_qs': stats.get('qs'),
                'is_pitcher': True, 'is_closer': stats.get('is_closer', False),
                'drafted_by': None, 'pick_number': None, 'round': None,
            }
        else:
            # Position priority: ESPN (most accurate) → Fangraphs fielding → UTIL
            espn_pid = espn_name_map.get(lname)
            espn_pos = espn_id_map.get(espn_pid, {}).get('positions') if espn_pid else None
            # Strip any pitcher slots from hitters (some players have pitching
            # appearances in Fangraphs fielding data which leaks SP/RP eligibility)
            if espn_pos:
                espn_pos = [p for p in espn_pos if p not in ('SP', 'RP')]
            positions = espn_pos or [p for p in (fielding_positions.get(lname) or []) if p not in ('SP', 'RP')] or ['UTIL']
            adp = adp_override.get(lname, float(unified_rank))
            pool[lname] = {
                'name': stats['name'],
                'positions': positions,
                'adp': adp,
                'fantasy_score': score,
                'proj_avg': stats.get('avg'), 'proj_hr': stats.get('hr'),
                'proj_rbi': stats.get('rbi'), 'proj_r': stats.get('r'),
                'proj_sb': stats.get('sb'), 'proj_xbh': stats.get('xbh'),
                'proj_era': None, 'proj_whip': None, 'proj_k': None,
                'proj_w': None, 'proj_sv': None, 'proj_holds': None, 'proj_qs': None,
                'is_pitcher': False, 'is_closer': False,
                'drafted_by': None, 'pick_number': None, 'round': None,
            }

    # Apply ADP overrides (manual adp.csv wins over computed rank)
    for lname in pool:
        if lname in adp_override:
            pool[lname]['adp'] = adp_override[lname]

    print(f"\nPlayer pool ready: {len(pool)} players")
    return pool


def mark_drafted(pool: Dict[str, dict], player_name: str,
                 team_name: str, pick_number: int, round_num: int) -> Optional[str]:
    """
    Mark player as drafted. Fuzzy-matches name. Returns matched key or None.
    """
    lname = player_name.lower().strip()

    # Exact match first
    if lname in pool:
        pool[lname].update({'drafted_by': team_name, 'pick_number': pick_number, 'round': round_num})
        return lname

    # Partial match — handle "Lastname, Firstname" vs "Firstname Lastname"
    parts = lname.split()
    for key in pool:
        if pool[key]['drafted_by'] is not None:
            continue
        key_parts = key.split()
        # Check if last name matches and is not too generic
        if parts and key_parts and parts[-1] == key_parts[-1] and len(parts[-1]) > 3:
            pool[key].update({'drafted_by': team_name, 'pick_number': pick_number, 'round': round_num})
            return key
        # Substring match
        if lname in key or key in lname:
            pool[key].update({'drafted_by': team_name, 'pick_number': pick_number, 'round': round_num})
            return key

    # Unknown player — add to pool as drafted
    pool[lname] = {
        'name': player_name, 'positions': ['UTIL'], 'adp': 999, 'fantasy_score': 0,
        'proj_avg': None, 'proj_hr': None, 'proj_rbi': None, 'proj_r': None,
        'proj_sb': None, 'proj_xbh': None,
        'proj_era': None, 'proj_whip': None, 'proj_k': None, 'proj_w': None,
        'proj_sv': None, 'proj_holds': None, 'proj_qs': None,
        'is_pitcher': False, 'is_closer': False,
        'drafted_by': team_name, 'pick_number': pick_number, 'round': round_num,
    }
    return lname


def get_available(pool: Dict[str, dict]) -> Dict[str, dict]:
    """Return only undrafted players, sorted by ADP."""
    return {k: v for k, v in pool.items() if v['drafted_by'] is None}


if __name__ == '__main__':
    pool = build_player_pool()
    available = get_available(pool)
    print(f"\nTop 20 by ADP:")
    top20 = sorted(available.values(), key=lambda p: p['adp'])[:20]
    for p in top20:
        pos = '/'.join(p['positions'][:2])
        print(f"  {p['adp']:5.1f}  {p['name']:<25} {pos}")
