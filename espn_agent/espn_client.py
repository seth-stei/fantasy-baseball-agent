#!/usr/bin/env python3
"""
ESPN Fantasy Baseball API client.
Reads roster, matchups, standings, and free agents from ESPN.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from espn_api.baseball import League


LEAGUE_ID = int(os.getenv('ESPN_LEAGUE_ID', '1007942881'))
YEAR = int(os.getenv('ESPN_YEAR', '2026'))
ESPN_S2 = os.getenv('ESPN_S2', '')
ESPN_SWID = os.getenv('ESPN_SWID', '')
MY_TEAM_NAME = os.getenv('ESPN_TEAM_NAME', '')


class ESPNClient:
    def __init__(self):
        self.league = League(
            league_id=LEAGUE_ID,
            year=YEAR,
            espn_s2=ESPN_S2,
            swid=ESPN_SWID
        )
        self.my_team = self._find_my_team()

    def _find_my_team(self):
        if MY_TEAM_NAME:
            for team in self.league.teams:
                if MY_TEAM_NAME.lower() in team.team_name.lower():
                    return team
        # Fall back to first team if no name configured
        print("⚠️  ESPN_TEAM_NAME not set - using first team. Set ESPN_TEAM_NAME in .env")
        return self.league.teams[0]

    def get_my_roster(self):
        """Return list of players on my roster."""
        return self.my_team.roster

    def get_matchup(self):
        """Return current matchup box score for my team."""
        try:
            box_scores = self.league.box_scores()
            for box in box_scores:
                if box.home_team == self.my_team or box.away_team == self.my_team:
                    return box
        except Exception:
            pass
        return None

    def get_free_agents(self, size=50, position=None):
        """Return top available free agents, optionally filtered by position."""
        try:
            return self.league.free_agents(size=size)
        except Exception as e:
            print(f"  Warning: could not fetch free agents: {e}")
            return []

    def get_standings(self):
        """Return league standings (list of teams sorted by rank)."""
        return self.league.standings()

    def get_all_teams(self):
        """Return all teams in the league."""
        return self.league.teams

    def get_scoring_categories(self):
        """Return the league's scoring categories."""
        try:
            return self.league.settings.stat_categories
        except Exception:
            return []

    def get_league_summary(self):
        """Print a summary of the league for debugging."""
        league_name = getattr(self.league.settings, 'name', None) or getattr(self.league.settings, 'league_name', f"League {LEAGUE_ID}")
        print(f"\nLeague: {league_name}")
        print(f"Teams: {len(self.league.teams)}")
        print(f"My team: {self.my_team.team_name}")
        print(f"Record: {self.my_team.wins}-{self.my_team.losses}")
        print(f"\nAll teams:")
        for i, team in enumerate(self.league.standings()):
            print(f"  {i+1}. {team.team_name} ({team.wins}-{team.losses})")


if __name__ == '__main__':
    # Test connection
    print("Connecting to ESPN...")
    try:
        client = ESPNClient()
        client.get_league_summary()

        print(f"\nMy roster ({len(client.get_my_roster())} players):")
        for player in client.get_my_roster():
            status = f" [{player.injuryStatus}]" if hasattr(player, 'injuryStatus') and player.injuryStatus != 'ACTIVE' else ""
            print(f"  {player.name:<25} {player.position}{status}")
        print("\n✓ ESPN connection successful")
    except Exception as e:
        print(f"✗ ESPN connection failed: {e}")
        print("\nMake sure ESPN_S2 and ESPN_SWID are set in your .env file.")
        print("Get them from espn.com > DevTools > Application > Cookies")
