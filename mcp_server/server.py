"""NBA MCP server.

This is a small MCP server. It "wraps" real, free NBA data sources and exposes
them as MCP *tools*:

| Tool                   | Data source                          |
|------------------------|--------------------------------------|
| get_player_stats       | stats.nba.com (via `nba_api`)        |
| get_player_game_log    | stats.nba.com (via `nba_api`)        |
| compare_players        | stats.nba.com (via `nba_api`)        |
| get_team_standings     | stats.nba.com (via `nba_api`)        |
| get_todays_games       | cdn.nba.com live scoreboard          |
| search_news            | free RSS feeds (ESPN, CBS, Yahoo)    |

Any MCP client (our LangGraph agent, Claude Desktop, MCP Inspector, ...) can
connect to it and call these tools. The agent does not need to know anything
about nba_api. It only sees the tool names, descriptions (the docstrings!) and
arguments. Good docstrings = the LLM picks the right tool.

Run locally:
    python server.py
It listens on http://localhost:8001/mcp (transport: "streamable-http").
"""

import datetime as dt
import email.utils
import functools
import html
import logging
import math
import os
import re
import unicodedata
import xml.etree.ElementTree as ET

import httpx
from cache import throttle, ttl_cache
from mcp.server.fastmcp import FastMCP
from nba_api.live.nba.endpoints import scoreboard
from nba_api.stats.endpoints import leaguestandingsv3, playercareerstats, playergamelog
from nba_api.stats.static import players

log = logging.getLogger("nba_mcp")

mcp = FastMCP(
    "nba",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8001")),
)

NBA_TIMEOUT = 30  # seconds to wait for stats.nba.com

# How long to keep each kind of data (seconds).
CAREER_TTL = 6 * 60 * 60  # season averages change at most once a day
GAME_LOG_TTL = 30 * 60
STANDINGS_TTL = 30 * 60
SCOREBOARD_TTL = 30  # live scores change fast
NEWS_TTL = 10 * 60

NEWS_FEEDS = {
    "ESPN": "https://www.espn.com/espn/rss/nba/news",
    "CBS Sports": "https://www.cbssports.com/rss/headlines/nba/",
    "Yahoo Sports": "https://sports.yahoo.com/nba/rss.xml",
}

# Common nicknames -> full name. Fans type these a lot.
NICKNAMES = {
    "steph": "Stephen Curry",
    "chef curry": "Stephen Curry",
    "king james": "LeBron James",
    "lbj": "LeBron James",
    "kd": "Kevin Durant",
    "the greek freak": "Giannis Antetokounmpo",
    "greek freak": "Giannis Antetokounmpo",
    "the joker": "Nikola Jokic",
    "joker": "Nikola Jokic",
    "mj": "Michael Jordan",
    "kobe": "Kobe Bryant",
    "shaq": "Shaquille O'Neal",
    "the brow": "Anthony Davis",
    "ad": "Anthony Davis",
    "cp3": "Chris Paul",
    "dame": "Damian Lillard",
    "sga": "Shai Gilgeous-Alexander",
    "wemby": "Victor Wembanyama",
    "ant": "Anthony Edwards",
    "luka": "Luka Doncic",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def tool_errors_as_data(fn):
    """Decorator: if a tool crashes, return {"error": ...} instead.

    Concept: the LLM reads tool results. A clear error message lets it tell the
    user "I couldn't get that" instead of guessing numbers.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:  # bad input from the LLM, e.g. a wrong season
            return {"error": str(e)}
        except Exception as e:  # network problems, NBA.com rate limits, ...
            log.exception("Tool %s failed", fn.__name__)
            return {
                "error": f"The NBA data source failed ({type(e).__name__}). "
                "Do not guess numbers. Tell the user to try again later."
            }

    return wrapper


def current_season(today: dt.date | None = None) -> str:
    """The NBA season for a date. Seasons start in October: Nov 2026 -> "2026-27"."""
    today = today or dt.datetime.now().astimezone().date()
    start = today.year if today.month >= 10 else today.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def normalize_season(season: str | None) -> str | None:
    """Turn "2015-2016", "2015/16" or "2015-16" into "2015-16". None stays None."""
    if season is None or not season.strip() or season.strip().lower() in ("latest", "current"):
        return None
    s = season.strip()
    m = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{2}|\d{4})", s)
    if m:
        start, end = int(m[1]), int(m[2][-2:])
        if (start + 1) % 100 != end:
            raise ValueError(f'"{season}" is not a real season. Use "YYYY-YY", e.g. "2015-16".')
        return f"{start}-{end:02d}"
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        raise ValueError(
            f'"{season}" is ambiguous. Use "YYYY-YY": "{y - 1}-{y % 100:02d}" is the season '
            f'that ended in {y}, "{y}-{(y + 1) % 100:02d}" is the one that started in {y}.'
        )
    raise ValueError(f'Bad season "{season}". Use "YYYY-YY", e.g. "2015-16".')


def _norm(text: str) -> str:
    """Lowercase, remove accents and dots: "Nikola Jokić" -> "nikola jokic"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[.\s]+", " ", text.lower()).strip()


def find_player(name: str) -> dict:
    """Find one player by name. Raises ValueError if none or too many match."""
    q = _norm(name)
    q = _norm(NICKNAMES.get(q, q))
    if not q:
        raise ValueError("Please give a player name.")
    everyone = players.get_players()  # a local list, no network call

    exact = [p for p in everyone if _norm(p["full_name"]) == q]
    if exact:
        # Same name twice in history (rare): prefer the active player.
        return min(exact, key=lambda p: not p["is_active"])

    # Every word typed must start a word of the name: "steph cur" -> Stephen Curry.
    words = q.split()

    def matches(p):
        name_words = _norm(p["full_name"]).split()
        return all(any(nw.startswith(w) for nw in name_words) for w in words)

    found = [p for p in everyone if matches(p)]
    if len(found) == 1:
        return found[0]
    active = [p for p in found if p["is_active"]]
    if len(active) == 1 and len(words) > 1:
        return active[0]
    if not found:
        raise ValueError(f'No NBA player found for "{name}". Check the spelling.')
    names = [p["full_name"] for p in sorted(found, key=lambda p: not p["is_active"])][:10]
    raise ValueError(f'"{name}" matches several players: {", ".join(names)}. Use the full name.')


def _r(value, digits: int = 1):
    """Round a number for the LLM. NaN (missing) becomes None."""
    if value is None or math.isnan(value):
        return None
    return round(float(value), digits)


def _int(value):
    """Whole number, or None if missing."""
    rounded = _r(value, 0)
    return None if rounded is None else int(rounded)


# --------------------------------------------------------------------------
# Data fetching (cached). These are the only functions that call NBA.com.
# --------------------------------------------------------------------------


@ttl_cache(CAREER_TTL)
def fetch_career(player_id: int) -> dict:
    throttle()
    career = playercareerstats.PlayerCareerStats(
        player_id=player_id, per_mode36="PerGame", timeout=NBA_TIMEOUT
    )
    return {
        "regular": career.season_totals_regular_season.get_data_frame(),
        "playoffs": career.season_totals_post_season.get_data_frame(),
        "career_regular": career.career_totals_regular_season.get_data_frame(),
        "career_playoffs": career.career_totals_post_season.get_data_frame(),
    }


@ttl_cache(GAME_LOG_TTL)
def fetch_game_log(player_id: int, season: str, season_type: str):
    throttle()
    return playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star=season_type,
        timeout=NBA_TIMEOUT,
    ).get_data_frames()[0]


@ttl_cache(STANDINGS_TTL)
def fetch_standings(season: str):
    throttle()
    return leaguestandingsv3.LeagueStandingsV3(
        season=season, season_type="Regular Season", timeout=NBA_TIMEOUT
    ).standings.get_data_frame()


@ttl_cache(SCOREBOARD_TTL)
def fetch_scoreboard() -> dict:
    return scoreboard.ScoreBoard(timeout=NBA_TIMEOUT).get_dict()["scoreboard"]


@ttl_cache(NEWS_TTL)
def fetch_feed(url: str) -> str:
    res = httpx.get(
        url, timeout=10, follow_redirects=True, headers={"User-Agent": "nba-agent/0.1"}
    )
    res.raise_for_status()
    return res.text


# --------------------------------------------------------------------------
# Logic shared by several tools
# --------------------------------------------------------------------------


def _stat_line(row) -> dict:
    """One row of per-game numbers -> a clean dict."""
    return {
        "games": int(row["GP"]),
        "games_started": _int(row.get("GS")),
        "minutes": _r(row["MIN"]),
        "points": _r(row["PTS"]),
        "rebounds": _r(row["REB"]),
        "assists": _r(row["AST"]),
        "steals": _r(row["STL"]),
        "blocks": _r(row["BLK"]),
        "turnovers": _r(row["TOV"]),
        "fg_pct": _r(row["FG_PCT"], 3),
        "fg3_pct": _r(row["FG3_PCT"], 3),
        "ft_pct": _r(row["FT_PCT"], 3),
    }


def season_averages(player_name: str, season: str | None, playoffs: bool) -> dict:
    player = find_player(player_name)
    data = fetch_career(player["id"])
    kind = "Playoffs" if playoffs else "Regular Season"

    if season and season.strip().lower() == "career":
        rows = data["career_playoffs" if playoffs else "career_regular"]
        if rows.empty:
            return {"player": player["full_name"], "error": f"No {kind} career stats."}
        return {"player": player["full_name"], "season": "career", "season_type": kind,
                **_stat_line(rows.iloc[0])}

    season = normalize_season(season)
    rows = data["playoffs" if playoffs else "regular"]
    if rows.empty:
        return {"player": player["full_name"], "error": f"No {kind} stats found."}
    season = season or rows["SEASON_ID"].iloc[-1]  # None -> latest season played
    rows = rows[rows["SEASON_ID"] == season]
    if rows.empty:
        return {"player": player["full_name"], "error": f"No {kind} stats for {season}. "
                "He may not have played that season."}

    # A traded player has one row per team plus a "TOT" (total) row.
    teams = [t for t in rows["TEAM_ABBREVIATION"] if t != "TOT"]
    total = rows[rows["TEAM_ABBREVIATION"] == "TOT"]
    row = (total if not total.empty else rows).iloc[0]
    return {
        "player": player["full_name"],
        "season": season,
        "season_type": kind,
        "team": "/".join(teams),
        "age": _int(row.get("PLAYER_AGE")),
        **_stat_line(row),
    }


# --------------------------------------------------------------------------
# MCP tools. The docstring is what the LLM reads to choose a tool.
# --------------------------------------------------------------------------


@mcp.tool()
@tool_errors_as_data
def get_player_stats(player_name: str, season: str | None = None, playoffs: bool = False) -> dict:
    """Get an NBA player's per-game averages for one season (points, rebounds,
    assists, steals, blocks, shooting %).

    Use for questions like "How many points did Curry average in 2015-16?".

    Args:
        player_name: Full name works best, e.g. "Stephen Curry". Nicknames like
            "Steph" or "KD" also work.
        season: "YYYY-YY", e.g. "2015-16". Leave empty for the latest season he
            played. Use "career" for career averages.
        playoffs: True for playoff stats instead of regular season.
    """
    return season_averages(player_name, season, playoffs)


@mcp.tool()
@tool_errors_as_data
def get_player_game_log(
    player_name: str,
    season: str | None = None,
    last_n: int = 5,
    sort_by: str = "date",
    playoffs: bool = False,
) -> dict:
    """Get a player's box scores game by game (points, rebounds, assists, ...).

    Use for "How did LeBron play in his last 3 games?" (sort_by="date") or
    "What was Kobe's best scoring game in 2005-06?" (sort_by="points").

    Args:
        player_name: e.g. "LeBron James".
        season: "YYYY-YY". Leave empty for the current season.
        last_n: How many games to return (1-20).
        sort_by: "date" (newest first) or "points" (highest first).
        playoffs: True for playoff games.
    """
    player = find_player(player_name)
    season = normalize_season(season) or current_season()
    last_n = max(1, min(int(last_n), 20))
    if sort_by not in ("date", "points"):
        raise ValueError('sort_by must be "date" or "points".')
    kind = "Playoffs" if playoffs else "Regular Season"

    rows = fetch_game_log(player["id"], season, kind)
    if rows.empty:
        return {"player": player["full_name"], "season": season,
                "error": f"No {kind} games found for {season}."}
    if sort_by == "points":
        rows = rows.sort_values("PTS", ascending=False, kind="stable")
    rows = rows.head(last_n)

    games = [
        {
            "date": _iso_date(r["GAME_DATE"]),
            "matchup": r["MATCHUP"],
            "result": r["WL"],
            "minutes": _r(r["MIN"], 0),
            "points": int(r["PTS"]),
            "rebounds": int(r["REB"]),
            "assists": int(r["AST"]),
            "steals": int(r["STL"]),
            "blocks": int(r["BLK"]),
            "turnovers": int(r["TOV"]),
            "fgm_fga": f"{int(r['FGM'])}-{int(r['FGA'])}",
            "fg3m_fg3a": f"{int(r['FG3M'])}-{int(r['FG3A'])}",
            "plus_minus": _r(r["PLUS_MINUS"], 0),
        }
        for _, r in rows.iterrows()
    ]
    return {
        "player": player["full_name"],
        "season": season,
        "season_type": kind,
        "sorted_by": sort_by,
        "games": games,
        "average_of_these_games": {
            stat: _r(sum(g[stat] for g in games) / len(games))
            for stat in ("points", "rebounds", "assists")
        },
    }


def _iso_date(text: str) -> str:
    """ "APR 13, 2016" -> "2016-04-13". Leaves other formats as they are."""
    try:
        return dt.datetime.strptime(text.strip() + " +0000", "%b %d, %Y %z").date().isoformat()
    except ValueError:
        return text


HIGHER_IS_BETTER = ["points", "rebounds", "assists", "steals", "blocks",
                    "fg_pct", "fg3_pct", "ft_pct"]


@mcp.tool()
@tool_errors_as_data
def compare_players(
    player_a: str, player_b: str, season: str | None = None, playoffs: bool = False
) -> dict:
    """Compare two players' per-game averages side by side, with the difference
    and who is better in each stat.

    Use for "Who was better in 2015-16, Curry or LeBron?" or "Compare Jokic and Embiid".

    Args:
        player_a: First player's name.
        player_b: Second player's name.
        season: "YYYY-YY". Leave empty to use each player's latest season.
            Use "career" for career averages.
        playoffs: True for playoff stats.
    """
    a = season_averages(player_a, season, playoffs)
    b = season_averages(player_b, season, playoffs)
    for p in (a, b):
        if "error" in p:
            return {"error": f"{p['player']}: {p['error']}", "player_a": a, "player_b": b}

    better, difference = {}, {}
    for stat in HIGHER_IS_BETTER + ["turnovers"]:
        va, vb = a[stat], b[stat]
        if va is None or vb is None:
            continue
        digits = 3 if stat.endswith("_pct") else 1
        difference[stat] = round(va - vb, digits)
        if va != vb:
            a_wins = (va > vb) if stat != "turnovers" else (va < vb)
            better[stat] = a["player"] if a_wins else b["player"]
        else:
            better[stat] = "tie"
    return {
        "player_a": a,
        "player_b": b,
        "difference_a_minus_b": difference,
        "better": better,
    }


@mcp.tool()
@tool_errors_as_data
def get_team_standings(
    season: str | None = None, conference: str | None = None, team: str | None = None
) -> dict:
    """Get NBA regular-season standings: wins, losses, rank, games back, streak.

    Use for "Who is first in the West?" or "What was the Warriors' record in 2015-16?".

    Args:
        season: "YYYY-YY". Leave empty for the current season.
        conference: "East" or "West". Leave empty for both.
        team: Optional team name or city to return only that team, e.g. "Warriors".
    """
    season = normalize_season(season) or current_season()
    rows = fetch_standings(season)
    if rows.empty:
        return {"season": season, "error": f"No standings for {season}."}

    teams = [
        {
            "rank": int(r["PlayoffRank"]),
            "team": f"{r['TeamCity']} {r['TeamName']}",
            "conference": r["Conference"],
            "wins": int(r["WINS"]),
            "losses": int(r["LOSSES"]),
            "win_pct": _r(r["WinPCT"], 3),
            "games_back": r["ConferenceGamesBack"],
            "last_10": r["L10"],
            "streak": str(r["strCurrentStreak"]).strip(),
            "home": r["HOME"],
            "road": r["ROAD"],
        }
        for _, r in rows.iterrows()
    ]
    if conference:
        conf = conference.strip().lower()[:4]  # "eastern" -> "east"
        teams = [t for t in teams if t["conference"].lower().startswith(conf)]
    if team:
        q = _norm(team)
        teams = [t for t in teams if q in _norm(t["team"])]
        if not teams:
            raise ValueError(f'No team matches "{team}". Try a name like "Celtics".')
    teams.sort(key=lambda t: (t["conference"], t["rank"]))
    return {"season": season, "teams": teams}


@mcp.tool()
@tool_errors_as_data
def get_todays_games() -> dict:
    """List today's NBA games with the live score and status (e.g. "Q3 5:12", "Final")."""
    board = fetch_scoreboard()
    return {
        "date": board.get("gameDate"),
        "games": [
            {
                "home": g["homeTeam"]["teamTricode"],
                "away": g["awayTeam"]["teamTricode"],
                "home_score": g["homeTeam"]["score"],
                "away_score": g["awayTeam"]["score"],
                "status": g["gameStatusText"].strip(),
            }
            for g in board["games"]
        ],
    }


# --- News ----------------------------------------------------------------

STOP_WORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "news",
              "nba", "about", "latest", "any", "what", "is", "are", "with"}


def parse_feed(xml_text: str, source: str) -> list[dict]:
    """Read an RSS feed into a list of {title, url, published, summary, source}."""
    root = ET.fromstring(xml_text)
    articles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        published = None
        if item.findtext("pubDate"):
            try:
                published = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                pass
        summary = re.sub(r"<[^>]+>", "", html.unescape(item.findtext("description") or ""))
        articles.append({
            "title": html.unescape(title),
            "url": (item.findtext("link") or "").strip(),
            "published": published,
            "summary": summary.strip()[:300],
            "source": source,
        })
    return articles


@mcp.tool()
@tool_errors_as_data
def search_news(query: str = "", limit: int = 8) -> dict:
    """Get the latest NBA news headlines (from ESPN, CBS Sports and Yahoo RSS feeds).

    Use for trades, injuries, rumors and anything "recent" that stats can't answer.

    Args:
        query: Words to look for, e.g. "Lakers trade" or "Embiid injury".
            Leave empty for the top headlines.
        limit: Max number of headlines (1-20).
    """
    limit = max(1, min(int(limit), 20))
    articles, failed = [], []
    for source, url in NEWS_FEEDS.items():
        try:
            articles += parse_feed(fetch_feed(url), source)
        except (httpx.HTTPError, ET.ParseError, OSError) as e:  # one bad feed is OK
            log.warning("News feed %s failed: %s", source, e)
            failed.append(source)
    if not articles:
        return {"error": "Could not load any news feed right now.", "failed_sources": failed}

    # Remove duplicates (same headline in two feeds).
    unique = {_norm(a["title"]): a for a in articles}.values()

    words = [w for w in _norm(query).split() if w not in STOP_WORDS]

    def score(a):
        text = _norm(a["title"] + " " + a["summary"])
        return sum(w in text for w in words)

    oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    ranked = sorted(unique, key=lambda a: (score(a), a["published"] or oldest), reverse=True)
    if words:
        ranked = [a for a in ranked if score(a) > 0]

    result = {
        "query": query,
        "articles": [
            {**a, "published": a["published"].isoformat() if a["published"] else None}
            for a in ranked[:limit]
        ],
    }
    if not result["articles"]:
        result["note"] = f'No recent headline mentions "{query}".'
    if failed:
        result["failed_sources"] = failed
    return result


class _HideClientDisconnectNoise(logging.Filter):
    """mcp 1.x logs a scary traceback ("Error in standalone SSE writer ...
    ClosedResourceError") each time a client closes its session. That is normal
    (our agent opens a short session per tool call), so we hide it."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return type(exc).__name__ != "ClosedResourceError"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("mcp.server.streamable_http").addFilter(_HideClientDisconnectNoise())
    mcp.run(transport="streamable-http")
