"""Tests for the MCP tools. All NBA.com / RSS calls are mocked (see conftest.py).

Run from the repo root:  pytest
"""

import asyncio
import datetime as dt
from typing import ClassVar

import pandas as pd
import pytest
import server

CURRY, LEBRON = 201939, 2544

CAREER_COLS = ["SEASON_ID", "TEAM_ABBREVIATION", "PLAYER_AGE", "GP", "GS", "MIN", "PTS",
               "REB", "AST", "STL", "BLK", "TOV", "FG_PCT", "FG3_PCT", "FT_PCT"]


def season_row(season, team, pts, reb=5.0, ast=5.0, gp=79):
    return [season, team, 27, gp, gp, 34.2, pts, reb, ast, 1.0, 0.5, 3.0, 0.504, 0.454, 0.908]


SEASONS = {
    CURRY: [season_row("2014-15", "GSW", 23.8), season_row("2015-16", "GSW", 30.06, 5.4, 6.7)],
    LEBRON: [
        season_row("2015-16", "CLE", 25.3, 7.4, 6.8),
        # A traded season: one row per team, plus the "TOT" (total) row.
        season_row("2018-19", "AAA", 20.0, gp=30),
        season_row("2018-19", "BBB", 30.0, gp=30),
        season_row("2018-19", "TOT", 25.0, gp=60),
    ],
}


class _Table:
    def __init__(self, df):
        self.df = df

    def get_data_frame(self):
        return self.df


class FakeCareer:
    """Pretends to be nba_api's PlayerCareerStats."""

    calls = 0

    def __init__(self, player_id, per_mode36, timeout):
        FakeCareer.calls += 1
        df = pd.DataFrame(SEASONS.get(player_id, []), columns=CAREER_COLS)
        self.season_totals_regular_season = _Table(df)
        self.season_totals_post_season = _Table(df.iloc[0:0])
        self.career_totals_regular_season = _Table(df.drop(columns="SEASON_ID").tail(1))
        self.career_totals_post_season = _Table(df.iloc[0:0])


@pytest.fixture
def fake_career(monkeypatch):
    FakeCareer.calls = 0
    monkeypatch.setattr(server.playercareerstats, "PlayerCareerStats", FakeCareer)
    return FakeCareer


# ---------------------------------------------------------------- helpers


def test_current_season():
    assert server.current_season(dt.date(2026, 11, 24)) == "2026-27"
    assert server.current_season(dt.date(2027, 3, 1)) == "2026-27"
    assert server.current_season(dt.date(2027, 10, 1)) == "2027-28"


@pytest.mark.parametrize("raw, expected", [
    ("2015-16", "2015-16"), ("2015-2016", "2015-16"), ("2015/16", "2015-16"),
    ("1999-00", "1999-00"), (None, None), ("", None), ("latest", None),
])
def test_normalize_season(raw, expected):
    assert server.normalize_season(raw) == expected


@pytest.mark.parametrize("raw", ["2016", "2015-17", "last year"])
def test_normalize_season_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        server.normalize_season(raw)


@pytest.mark.parametrize("name, full_name", [
    ("Stephen Curry", "Stephen Curry"),
    ("stephen curry", "Stephen Curry"),
    ("Steph", "Stephen Curry"),          # nickname
    ("Nikola Jokic", "Nikola Jokić"),    # no accent typed
    ("giannis", "Giannis Antetokounmpo"),  # one unique word
    ("KD", "Kevin Durant"),
])
def test_find_player(name, full_name):
    assert server.find_player(name)["full_name"] == full_name


def test_find_player_ambiguous_lists_options():
    with pytest.raises(ValueError, match="several players") as err:
        server.find_player("Curry")
    assert "Stephen Curry" in str(err.value)


def test_find_player_unknown():
    with pytest.raises(ValueError, match="No NBA player"):
        server.find_player("Zxqwerty Blorp")


# ------------------------------------------------------- get_player_stats


def test_player_stats_for_a_season(fake_career):
    out = server.get_player_stats("Stephen Curry", "2015-16")
    assert out["player"] == "Stephen Curry"
    assert out["season"] == "2015-16"
    assert out["team"] == "GSW"
    assert out["points"] == 30.1  # rounded to 1 decimal
    assert out["fg3_pct"] == 0.454


def test_player_stats_latest_season_by_default(fake_career):
    assert server.get_player_stats("Stephen Curry")["season"] == "2015-16"


def test_player_stats_traded_player_uses_total_row(fake_career):
    out = server.get_player_stats("LeBron James", "2018-19")
    assert out["points"] == 25.0
    assert out["games"] == 60
    assert out["team"] == "AAA/BBB"


def test_player_stats_career(fake_career):
    assert server.get_player_stats("Stephen Curry", "career")["season"] == "career"


def test_player_stats_errors_are_returned_not_raised(fake_career):
    assert "error" in server.get_player_stats("Stephen Curry", "1990-91")
    assert "error" in server.get_player_stats("Stephen Curry", "2016")
    assert "error" in server.get_player_stats("Zxqwerty Blorp")
    assert "error" in server.get_player_stats("Stephen Curry", playoffs=True)


def test_network_failure_becomes_an_error_message(monkeypatch):
    def boom(**kwargs):
        raise TimeoutError("stats.nba.com timed out")

    monkeypatch.setattr(server.playercareerstats, "PlayerCareerStats", boom)
    out = server.get_player_stats("Stephen Curry")
    assert "failed" in out["error"]
    assert "Do not guess" in out["error"]


def test_cache_avoids_a_second_nba_call(fake_career):
    server.get_player_stats("Stephen Curry", "2015-16")
    server.get_player_stats("Stephen Curry", "2014-15")  # same player -> cached
    assert fake_career.calls == 1
    server.get_player_stats("LeBron James", "2015-16")
    assert fake_career.calls == 2


# ------------------------------------------------------- compare_players


def test_compare_players(fake_career):
    out = server.compare_players("Stephen Curry", "LeBron James", "2015-16")
    assert out["player_a"]["points"] == 30.1
    assert out["player_b"]["points"] == 25.3
    assert out["difference_a_minus_b"]["points"] == 4.8
    assert out["better"]["points"] == "Stephen Curry"
    assert out["better"]["rebounds"] == "LeBron James"
    assert out["better"]["turnovers"] == "tie"


def test_compare_players_with_missing_player(fake_career):
    assert "error" in server.compare_players("Stephen Curry", "Zxqwerty Blorp")


# --------------------------------------------------- get_player_game_log

GAME_COLS = ["GAME_DATE", "MATCHUP", "WL", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TOV",
             "FGM", "FGA", "FG3M", "FG3A", "PLUS_MINUS"]


class FakeGameLog:
    seen: ClassVar[dict] = {}

    def __init__(self, player_id, season, season_type_all_star, timeout):
        FakeGameLog.seen = {"season": season, "season_type": season_type_all_star}
        # nba_api returns the newest game first.
        self.df = pd.DataFrame([
            ["APR 13, 2006", "LAL vs. POR", "W", 40, 30, 5, 4, 1, 0, 2, 10, 20, 2, 5, 8],
            ["APR 11, 2006", "LAL @ SAC", "L", 42, 40, 6, 3, 2, 1, 3, 14, 28, 4, 9, -3],
            ["JAN 22, 2006", "LAL vs. TOR", "W", 42, 81, 6, 2, 3, 1, 3, 28, 46, 7, 13, 25],
        ], columns=GAME_COLS)

    def get_data_frames(self):
        return [self.df]


@pytest.fixture
def fake_game_log(monkeypatch):
    monkeypatch.setattr(server.playergamelog, "PlayerGameLog", FakeGameLog)


def test_game_log_newest_first(fake_game_log):
    out = server.get_player_game_log("Kobe Bryant", "2005-06", last_n=2)
    assert [g["points"] for g in out["games"]] == [30, 40]
    assert out["games"][0]["date"] == "2006-04-13"
    assert out["games"][0]["fgm_fga"] == "10-20"
    assert out["average_of_these_games"]["points"] == 35.0


def test_game_log_best_game(fake_game_log):
    out = server.get_player_game_log("Kobe Bryant", "2005-06", last_n=1, sort_by="points")
    assert out["games"][0]["points"] == 81
    assert out["games"][0]["matchup"] == "LAL vs. TOR"


def test_game_log_defaults_to_current_season(fake_game_log):
    server.get_player_game_log("Kobe Bryant")
    assert FakeGameLog.seen["season"] == server.current_season()


def test_game_log_bad_sort(fake_game_log):
    assert "error" in server.get_player_game_log("Kobe Bryant", sort_by="assists")


# ---------------------------------------------------- get_team_standings

STANDING_COLS = ["PlayoffRank", "TeamCity", "TeamName", "Conference", "WINS", "LOSSES",
                 "WinPCT", "ConferenceGamesBack", "L10", "strCurrentStreak", "HOME", "ROAD"]


class FakeStandings:
    def __init__(self, season, season_type, timeout):
        self.standings = _Table(pd.DataFrame([
            [1, "Golden State", "Warriors", "West", 73, 9, 0.89, 0.0, "8-2", "W 4", "39-2", "34-7"],
            [2, "San Antonio", "Spurs", "West", 67, 15, 0.817, 6.0, "7-3", "L 1", "40-1", "27-14"],
            [1, "Cleveland", "Cavaliers", "East", 57, 25, 0.695, 0.0, "6-4", "L 1", "33-8", "24-17"],
        ], columns=STANDING_COLS))


@pytest.fixture
def fake_standings(monkeypatch):
    monkeypatch.setattr(server.leaguestandingsv3, "LeagueStandingsV3", FakeStandings)


def test_standings_all(fake_standings):
    out = server.get_team_standings("2015-16")
    assert [t["team"] for t in out["teams"]] == [
        "Cleveland Cavaliers", "Golden State Warriors", "San Antonio Spurs"]


def test_standings_filter_conference_and_team(fake_standings):
    assert len(server.get_team_standings("2015-16", conference="Western")["teams"]) == 2
    team = server.get_team_standings("2015-16", team="warriors")["teams"]
    assert team[0]["wins"] == 73 and team[0]["losses"] == 9
    assert "error" in server.get_team_standings("2015-16", team="Sonics")


# ------------------------------------------------------ get_todays_games


def test_todays_games(monkeypatch):
    class FakeBoard:
        def __init__(self, timeout):
            pass

        def get_dict(self):
            return {"scoreboard": {"gameDate": "2026-11-24", "games": [{
                "homeTeam": {"teamTricode": "LAL", "score": 101},
                "awayTeam": {"teamTricode": "BOS", "score": 99},
                "gameStatusText": "Final ",
            }]}}

    monkeypatch.setattr(server.scoreboard, "ScoreBoard", FakeBoard)
    out = server.get_todays_games()
    assert out["date"] == "2026-11-24"
    assert out["games"][0] == {"home": "LAL", "away": "BOS", "home_score": 101,
                               "away_score": 99, "status": "Final"}


# ------------------------------------------------------------ search_news

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>NBA</title>
<item><title>Lakers make a big trade</title><link>https://example.com/1</link>
  <pubDate>Mon, 23 Nov 2026 18:00:00 GMT</pubDate>
  <description><![CDATA[<p>Los Angeles <b>Lakers</b> news.</p>]]></description></item>
<item><title>Embiid injury update</title><link>https://example.com/2</link>
  <pubDate>Mon, 23 Nov 2026 20:00:00 GMT</pubDate></item>
<item><title>Celtics win again</title><link>https://example.com/3</link>
  <pubDate>Sun, 22 Nov 2026 20:00:00 GMT</pubDate></item>
</channel></rss>"""


@pytest.fixture
def fake_feeds(monkeypatch):
    """Every feed returns the same RSS text, except CBS, which is down."""

    def fake_fetch(url):
        if "cbssports" in url:
            raise ConnectionError("down")
        return RSS

    monkeypatch.setattr(server, "fetch_feed", fake_fetch)


def test_parse_feed_cleans_html():
    articles = server.parse_feed(RSS, "ESPN")
    assert articles[0]["title"] == "Lakers make a big trade"
    assert articles[0]["summary"] == "Los Angeles Lakers news."
    assert articles[0]["published"].year == 2026


def test_news_filters_by_query_and_dedupes(fake_feeds):
    out = server.search_news("Lakers trade")
    assert [a["title"] for a in out["articles"]] == ["Lakers make a big trade"]
    assert out["failed_sources"] == ["CBS Sports"]


def test_news_without_query_is_newest_first(fake_feeds):
    titles = [a["title"] for a in server.search_news()["articles"]]
    assert titles == ["Embiid injury update", "Lakers make a big trade", "Celtics win again"]


def test_news_no_match(fake_feeds):
    out = server.search_news("Sonics")
    assert out["articles"] == [] and "note" in out


def test_news_all_feeds_down(monkeypatch):
    def down(url):
        raise ConnectionError("down")

    monkeypatch.setattr(server, "fetch_feed", down)
    assert "error" in server.search_news("Lakers")


# --------------------------------------------------------------- MCP layer


def test_all_tools_are_registered_with_descriptions():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {"get_player_stats", "get_player_game_log", "compare_players",
                          "get_team_standings", "get_todays_games", "search_news"}
    for tool in tools.values():
        assert tool.description and len(tool.description) > 40
    assert "player_name" in tools["get_player_stats"].inputSchema["properties"]


def test_disconnect_noise_filter():
    import logging

    import anyio

    def record(exc):
        return logging.LogRecord("mcp", logging.ERROR, "", 0, "msg", None,
                                 (type(exc), exc, None))

    noise_filter = server._HideClientDisconnectNoise()
    assert noise_filter.filter(record(anyio.ClosedResourceError())) is False
    assert noise_filter.filter(record(ValueError("real problem"))) is True
