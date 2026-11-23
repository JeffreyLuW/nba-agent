"""NBA MCP server.

This is a small MCP server. It "wraps" a real, free NBA data source (the
`nba_api` package, which reads stats.nba.com) and exposes it as MCP *tools*.

Any MCP client (our LangGraph agent, Claude Desktop, MCP Inspector, ...) can
connect to it and call these tools. The agent does not need to know anything
about nba_api. It only sees the tool names, descriptions and arguments.

Run locally:
    python server.py
It listens on http://localhost:8001/mcp (transport: "streamable-http").
"""

import os

from mcp.server.fastmcp import FastMCP
from nba_api.live.nba.endpoints import scoreboard
from nba_api.stats.endpoints import playercareerstats
from nba_api.stats.static import players

mcp = FastMCP(
    "nba",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8001")),
)


@mcp.tool()
def get_player_stats(player_name: str, season: str | None = None) -> dict:
    """Get regular-season per-game stats for an NBA player.

    Args:
        player_name: Full or partial name, e.g. "Stephen Curry".
        season: Season like "2024-25". If empty, return the latest season.
    """
    matches = players.find_players_by_full_name(player_name)
    if not matches:
        return {"error": f"No player found for '{player_name}'."}
    player = matches[0]

    career = playercareerstats.PlayerCareerStats(
        player_id=player["id"], per_mode36="PerGame", timeout=30
    )
    rows = career.season_totals_regular_season.get_data_frame()
    if rows.empty:
        return {"player": player["full_name"], "error": "No stats found."}

    if season:
        rows = rows[rows["SEASON_ID"] == season]
        if rows.empty:
            return {"player": player["full_name"], "error": f"No stats for {season}."}
    row = rows.iloc[-1]

    return {
        "player": player["full_name"],
        "season": row["SEASON_ID"],
        "team": row["TEAM_ABBREVIATION"],
        "games": int(row["GP"]),
        "points": float(row["PTS"]),
        "rebounds": float(row["REB"]),
        "assists": float(row["AST"]),
        "fg_pct": float(row["FG_PCT"]),
        "fg3_pct": float(row["FG3_PCT"]),
    }


@mcp.tool()
def get_todays_games() -> list[dict]:
    """List today's NBA games with the current score and status."""
    games = scoreboard.ScoreBoard().get_dict()["scoreboard"]["games"]
    return [
        {
            "home": g["homeTeam"]["teamTricode"],
            "away": g["awayTeam"]["teamTricode"],
            "home_score": g["homeTeam"]["score"],
            "away_score": g["awayTeam"]["score"],
            "status": g["gameStatusText"],
        }
        for g in games
    ]


@mcp.tool()
def search_news(query: str) -> list[dict]:
    """Search recent NBA news. (STUB: returns fake data for now.)

    Args:
        query: What to search for, e.g. "Lakers trade".
    """
    # TODO: connect a real news source (for example an RSS feed).
    return [{"title": f"Placeholder headline about {query}", "url": "https://example.com"}]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
