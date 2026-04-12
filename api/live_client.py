"""
Riot Live Client Data API wrapper.

The game runs a local HTTPS server at https://127.0.0.1:2999 while a match is
active.  It uses a self-signed certificate so SSL verification is disabled.

This module is the ONLY place in the codebase that performs HTTP calls.
All other modules receive already-parsed data structures.

Key facts about the API:
  - /allgamedata returns REAL-TIME HP for ALL players, including fog-of-war.
    This is intentional – Riot designed it for overlay tools.
  - /activeplayer returns local player stats (gold, abilities, stats).
  - /eventdata returns objective and kill events.
  - The API is only available while a game is in progress.
"""

import requests
import urllib3
from typing import Optional

import config

# Suppress InsecureRequestWarning from the self-signed cert
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_SESSION = requests.Session()
_SESSION.verify = False

# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

def _get(path: str) -> Optional[dict]:
    """
    GET a Live Client endpoint. Returns parsed JSON or None on failure.
    Never raises – callers treat None as 'game not running / no data'.
    """
    try:
        r = _SESSION.get(
            f"{config.API_BASE}{path}",
            timeout=config.API_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        return None   # Game not running or loading screen
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public endpoint accessors
# ---------------------------------------------------------------------------

def get_all_game_data() -> Optional[dict]:
    """
    Full game snapshot.  Top-level keys:
      activePlayer  – local player name, gold, championStats, abilities
      allPlayers    – list of all 10 players with HP, level, items, spells
      gameData      – gameTime, gameMode, mapName
      events        – Events list (kills, objectives, etc.)
    """
    return _get("/liveclientdata/allgamedata")


def get_active_player() -> Optional[dict]:
    """Local player: summonerName, currentGold, championStats, abilities."""
    return _get("/liveclientdata/activeplayer")


def get_player_list() -> Optional[list]:
    """All 10 players with real-time HP, level, items, summoner spells, team."""
    return _get("/liveclientdata/playerlist")


def get_game_stats() -> Optional[dict]:
    """gameTime (seconds), gameMode, mapName, mapNumber, etc."""
    return _get("/liveclientdata/gamestats")


def get_events() -> Optional[dict]:
    """All events so far: kills, dragon/baron/herald kills, etc."""
    return _get("/liveclientdata/eventdata")


def is_game_running() -> bool:
    """Return True when the Live Client API responds (game is live)."""
    return get_game_stats() is not None


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def hp_percent(player: dict) -> float:
    """
    Compute HP percentage (0–100) from a player dict.
    Returns 100.0 if data is missing/invalid to avoid false 'low HP' reads.
    """
    current = player.get("currentHealth", 0)
    maximum = player.get("maxHealth", 0)
    if maximum <= 0 or current < 0:
        return 100.0   # guard against division by zero or corrupt data
    return min(100.0, max(0.0, (current / maximum) * 100.0))


def is_player_dead(player: dict) -> bool:
    """True if the player has 0 or negative current health in the snapshot."""
    return player.get("currentHealth", 1) <= 0


def has_smite(player: dict) -> bool:
    """
    Return True when the player dict shows Smite as a summoner spell.
    Checks both the displayName and rawDisplayName fields because the API
    sometimes returns either format depending on game version.
    """
    _SMITE_IDS = {
        "SummonerSmite",
        "S5_SummonerSmiteDuel",
        "SummonerSmiteAvatarOffensive",
        "SummonerSmiteAvatarUtility",
        "SummonerSmiteAvatarDefensive",
        "SummonerSmitePlayerGanker",
    }
    spells = player.get("summonerSpells", {})
    for key in ("summonerSpellOne", "summonerSpellTwo"):
        spell = spells.get(key, {})
        raw  = spell.get("rawDisplayName", "")
        disp = spell.get("displayName", "")
        if raw in _SMITE_IDS or "Smite" in disp:
            return True
    return False


def get_items(player: dict) -> list:
    """Return the list of item dicts for a player, safe against missing data."""
    return player.get("items", [])


def get_champion_name(player: dict) -> str:
    return player.get("championName", "")


def get_summoner_name(player: dict) -> str:
    return player.get("summonerName", "")


def get_team(player: dict) -> str:
    """Returns 'ORDER' (blue) or 'CHAOS' (red)."""
    return player.get("team", "")


def get_role(player: dict) -> str:
    """Returns 'TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY', or ''."""
    return player.get("position", "")


def get_level(player: dict) -> int:
    return player.get("level", 1)


def get_scores(player: dict) -> dict:
    """Returns kills / deaths / assists / cs from the scores sub-dict."""
    return player.get("scores", {})
