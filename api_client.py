"""
Riot Live Client Data API wrapper.

The game exposes a local HTTPS server at https://127.0.0.1:2999 while a match
is in progress.  It uses a self-signed certificate, so SSL verification is
disabled (requests.get(..., verify=False)).

Key endpoints used:
  /liveclientdata/allgamedata   – full snapshot every poll
  /liveclientdata/activeplayer  – local player stats (gold, level, etc.)
  /liveclientdata/playerlist    – all 10 players (HP, spells, position…)
  /liveclientdata/gamestats     – game time
  /liveclientdata/eventdata     – kills, objectives, etc.
"""

import requests
import urllib3
from typing import Optional

import config

# Suppress the InsecureRequestWarning that fires because the game uses a
# self-signed cert.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_SESSION = requests.Session()
_SESSION.verify = False


def _get(path: str) -> Optional[dict]:
    """GET a Live Client API endpoint.  Returns parsed JSON or None on failure."""
    try:
        resp = _SESSION.get(
            f"{config.API_BASE}{path}",
            timeout=config.API_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        # Game not running / loading screen
        return None
    except Exception:
        return None


# ── Public helpers ──────────────────────────────────────────────────────────

def get_all_game_data() -> Optional[dict]:
    """Full game snapshot.  Returns dict with keys: activePlayer, allPlayers, gameData, events."""
    return _get("/liveclientdata/allgamedata")


def get_active_player() -> Optional[dict]:
    """The local player (summonerName, currentGold, championStats, abilities…)."""
    return _get("/liveclientdata/activeplayer")


def get_player_list() -> Optional[list]:
    """List of all 10 players with HP, summoner spells, position, scores, team."""
    return _get("/liveclientdata/playerlist")


def get_game_stats() -> Optional[dict]:
    """Game metadata: gameTime (seconds), gameMode, mapName, etc."""
    return _get("/liveclientdata/gamestats")


def get_events() -> Optional[dict]:
    """All game events so far (kills, dragon kills, baron, etc.)."""
    return _get("/liveclientdata/eventdata")


def is_game_running() -> bool:
    """Return True when the Live Client API is reachable (game is active)."""
    return get_game_stats() is not None


# ── Data helpers ────────────────────────────────────────────────────────────

def has_smite(player: dict) -> bool:
    """Return True when a player dict contains Smite as a summoner spell."""
    smite_ids = {
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
        raw = spell.get("rawDisplayName", "")
        display = spell.get("displayName", "")
        if raw in smite_ids or "Smite" in display:
            return True
    return False


def hp_percent(player: dict) -> float:
    """Return current HP as a percentage (0–100)."""
    current = player.get("currentHealth", 0)
    maximum = player.get("maxHealth", 1)
    if maximum <= 0:
        return 100.0
    return min(100.0, (current / maximum) * 100.0)


def level_of(player: dict) -> int:
    return player.get("level", 1)


def get_game_time(game_data: dict) -> float:
    """Extract game time (seconds) from allgamedata snapshot."""
    return game_data.get("gameData", {}).get("gameTime", 0.0)
