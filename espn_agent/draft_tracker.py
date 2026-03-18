#!/usr/bin/env python3
"""
Live draft pick watcher.

Primary mode: polls ESPN's internal draft API every 10 seconds with your auth cookies.
Fallback:     manual entry — type "p: Player Name" to log a pick manually.

No Playwright or browser automation needed — ESPN's API is polled directly.
"""
import os
import sys
import time
import threading
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
from typing import Dict, List, Optional, Callable


LEAGUE_ID  = int(os.getenv('ESPN_LEAGUE_ID', '1007942881'))
YEAR       = int(os.getenv('ESPN_YEAR', '2026'))
ESPN_S2    = os.getenv('ESPN_S2', '')
ESPN_SWID  = os.getenv('ESPN_SWID', '')

ESPN_API_BASE = "https://fantasy.espn.com/apis/v3/games/flb"
POLL_INTERVAL = 10   # seconds between API polls


def get_snake_pick_numbers(draft_position: int, num_teams: int, num_rounds: int = 23) -> List[int]:
    """
    Return the list of overall pick numbers that belong to this draft position
    in a snake draft.

    E.g. for position=3, teams=10:
      Round 1: pick 3
      Round 2: pick 18  (10 - 3 + 1 = 8, offset by 10 → pick 18)
      Round 3: pick 23
      ...
    """
    picks = []
    for rnd in range(1, num_rounds + 1):
        if rnd % 2 == 1:  # odd round: normal order
            pick = (rnd - 1) * num_teams + draft_position
        else:             # even round: reversed
            pick = (rnd - 1) * num_teams + (num_teams - draft_position + 1)
        picks.append(pick)
    return picks


class DraftTracker:
    """
    Tracks the live draft by polling the ESPN API.

    Usage:
        tracker = DraftTracker(
            draft_position=5,
            num_teams=10,
            espn_id_map={player_id: {name, positions}, ...},
            team_id_map={team_id: team_name, ...},
        )
        tracker.start_polling()   # begins background thread
        ...
        new_picks = tracker.pop_new_picks()  # call this in your loop
        if tracker.is_my_turn():
            # show suggestions
    """

    def __init__(self,
                 draft_position: int,
                 num_teams: int,
                 espn_id_map: Dict[int, dict],
                 team_id_map: Dict[int, str],
                 num_rounds: int = 23,
                 my_team_id: Optional[int] = None):

        self.draft_position  = draft_position
        self.num_teams       = num_teams
        self.num_rounds      = num_rounds
        self.espn_id_map     = espn_id_map     # espn_player_id -> {name, positions}
        self.team_id_map     = team_id_map     # espn_team_id -> team_name

        # Compute my pick numbers for the whole draft
        self.my_pick_numbers = set(get_snake_pick_numbers(draft_position, num_teams, num_rounds))

        self._all_picks: List[dict] = []        # All picks seen so far (chronological)
        self._new_picks: List[dict] = []        # Picks since last pop_new_picks() call
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._api_available = True              # Set False if API consistently fails

        self.my_team_id = my_team_id
        self.my_picks: List[dict] = []          # My own picks (player dicts)

    # ─── Public API ───────────────────────────────────────────────────────────

    def start_polling(self):
        """Start background polling thread."""
        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self):
        """Stop the background thread."""
        self._stop_event.set()

    def pop_new_picks(self) -> List[dict]:
        """
        Return any new picks since last call, and clear the buffer.
        Each pick is: {player_name, team_name, pick_number, round, round_pick}
        """
        with self._lock:
            picks = list(self._new_picks)
            self._new_picks.clear()
        return picks

    def current_pick_number(self) -> int:
        """Overall pick number of the NEXT pick to happen."""
        return len(self._all_picks) + 1

    def current_round(self) -> int:
        """Current draft round (1-indexed)."""
        picks_done = len(self._all_picks)
        return (picks_done // self.num_teams) + 1

    def is_my_turn(self) -> bool:
        """True if the next pick belongs to my team."""
        return self.current_pick_number() in self.my_pick_numbers

    def is_draft_complete(self) -> bool:
        return len(self._all_picks) >= self.num_teams * self.num_rounds

    def get_my_picks(self) -> List[dict]:
        """Return list of players I've drafted so far."""
        return list(self.my_picks)

    def log_manual_pick(self, player_name: str, team_name: str = 'Unknown') -> dict:
        """
        Manually register a pick (used in --manual mode or when API fails).
        Returns the pick dict.
        """
        pick_num = self.current_pick_number()
        round_num = self.current_round()
        round_pick = ((pick_num - 1) % self.num_teams) + 1

        pick = {
            'player_name': player_name,
            'team_name': team_name,
            'pick_number': pick_num,
            'round': round_num,
            'round_pick': round_pick,
            'is_my_pick': pick_num in self.my_pick_numbers,
        }
        with self._lock:
            self._all_picks.append(pick)
            self._new_picks.append(pick)

        return pick

    # ─── Internal API polling ─────────────────────────────────────────────────

    def _poll_loop(self):
        """Background thread: poll ESPN API every POLL_INTERVAL seconds."""
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                new = self._fetch_new_picks()
                if new:
                    with self._lock:
                        self._all_picks.extend(new)
                        self._new_picks.extend(new)
                consecutive_failures = 0
                self._api_available = True
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    self._api_available = False
                # Don't print every failure to avoid spamming the terminal

            self._stop_event.wait(POLL_INTERVAL)

    def _fetch_new_picks(self) -> List[dict]:
        """
        Poll ESPN draft API and return picks we haven't seen yet.
        """
        url = f"{ESPN_API_BASE}/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
        params = {'view': 'mDraftDetail'}
        cookies = {'espn_s2': ESPN_S2, 'SWID': ESPN_SWID}
        headers = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'}

        resp = requests.get(url, params=params, cookies=cookies,
                            headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        api_picks = data.get('draftDetail', {}).get('picks', [])
        already_seen = len(self._all_picks)

        new_picks = []
        for raw in api_picks[already_seen:]:
            pick_num   = raw.get('id', 0)
            round_num  = raw.get('roundId', 1)
            round_pick = raw.get('roundPickNumber', 1)
            player_id  = raw.get('playerId', 0)
            team_id    = raw.get('teamId', 0)

            player_info = self.espn_id_map.get(player_id, {})
            player_name = player_info.get('name', f'Player #{player_id}')
            team_name   = self.team_id_map.get(team_id, f'Team #{team_id}')
            is_my_pick  = (team_id == self.my_team_id) if self.my_team_id else (pick_num in self.my_pick_numbers)

            new_picks.append({
                'player_name': player_name,
                'team_name':   team_name,
                'pick_number': pick_num,
                'round':       round_num,
                'round_pick':  round_pick,
                'is_my_pick':  is_my_pick,
                'player_id':   player_id,
                'team_id':     team_id,
            })

        return new_picks

    # ─── Quick connectivity test ───────────────────────────────────────────────

    def test_api_connection(self) -> bool:
        """
        Try to reach ESPN's draft API. Returns True if it responds.
        Called once at startup so we know whether to fall back to manual mode.
        """
        try:
            url = f"{ESPN_API_BASE}/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
            params = {'view': 'mDraftDetail'}
            cookies = {'espn_s2': ESPN_S2, 'SWID': ESPN_SWID}
            resp = requests.get(url, params=params, cookies=cookies, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            # Accept any valid JSON response — draftDetail may be absent before
            # pick 1, but will appear once the real draft starts.
            return isinstance(data, dict)
        except Exception:
            return False


def build_team_id_map(espn_league) -> Dict[int, str]:
    """
    Build {espn_team_id: team_name} from the espn-api League object.
    """
    team_map = {}
    for team in espn_league.teams:
        tid = getattr(team, 'team_id', None)
        if tid is not None:
            team_map[int(tid)] = team.team_name
    return team_map


def find_my_team_id(espn_league, my_team_name: str) -> Optional[int]:
    """Find my team's ESPN team_id from league teams."""
    for team in espn_league.teams:
        if my_team_name.lower() in team.team_name.lower():
            tid = getattr(team, 'team_id', None)
            return int(tid) if tid is not None else None
    return None


if __name__ == '__main__':
    # Test the snake draft order calculation
    print("Pick numbers for draft position 5, 10 teams, 23 rounds:")
    picks = get_snake_pick_numbers(5, 10, 23)
    for rnd, pick in enumerate(picks, 1):
        print(f"  Round {rnd:2d}: pick {pick:3d}")

    print("\nTesting ESPN API connection...")
    tracker = DraftTracker(
        draft_position=5,
        num_teams=10,
        espn_id_map={},
        team_id_map={},
    )
    ok = tracker.test_api_connection()
    print(f"  API available: {ok}")
