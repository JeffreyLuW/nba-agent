"""Tests for the eval checks (no LLM needed)."""

from checks import check_contains, check_tools, numbers_in, score_case, ungrounded_numbers

TOOL_OUT = [
    '{"player": "Stephen Curry", "season": "2015-16", "points": 30.06, "fg3_pct": 0.454, "games": 79}',
    '{"player": "LeBron James", "season": "2015-16", "points": 25.3}',
]


def test_numbers_in_skips_seasons_dates_and_times():
    text = "In 2015-16 (on 2016-04-13 at 7:30, Q3) he scored 30.1 and shot .454 on 1,234 tries"
    assert numbers_in(text) == ["30.1", ".454", "1234"]


def test_grounded_numbers_pass():
    answer = "Curry averaged 30.1 points (about 30) on 45.4% from three in 79 games."
    assert ungrounded_numbers(answer, TOOL_OUT) == []


def test_difference_of_tool_numbers_is_grounded():
    assert ungrounded_numbers("Curry scored 4.8 more points than LeBron.", TOOL_OUT) == []


def test_invented_number_is_caught():
    assert ungrounded_numbers("Curry averaged 31.5 points and 12 assists.", TOOL_OUT) == [
        "31.5", "12"]


def test_numbers_without_tools_are_caught():
    assert ungrounded_numbers("He averaged 27.1 points.", []) == ["27.1"]


def test_small_numbers_years_and_question_numbers_are_ignored():
    question = "How did LeBron play in his last 3 games in 2024?"
    assert ungrounded_numbers("In 2024 his last 3 games were great, 2 wins.", [], question) == []


def test_check_tools():
    calls = [{"name": "get_player_stats", "args": {"player_name": "Stephen Curry",
                                                    "season": "2015-16", "playoffs": True}}]
    case = {"expected_tools": ["get_player_stats"],
            "expected_args": {"get_player_stats": {"player_name": "curry", "playoffs": True}}}
    assert check_tools(case, calls) == []
    assert check_tools({"expected_tools": ["compare_players|get_player_stats"]}, calls) == []
    assert check_tools({"expected_tools": ["get_team_standings"]}, calls)
    assert check_tools({"expected_tools": []}, calls)  # should not call any tool
    wrong_season = {"expected_tools": ["get_player_stats"],
                    "expected_args": {"get_player_stats": {"season": "2016-17"}}}
    assert check_tools(wrong_season, calls)


def test_check_contains():
    assert check_contains({"answer_contains": ["30.1"]}, "It was 30.1 PPG") == []
    assert check_contains({"answer_contains": ["30.1"]}, "It was 30 PPG")
    assert check_contains({"answer_contains_any": ["could not", "no player"]},
                          "I could NOT find him") == []


def test_score_case_strips_thinking():
    case = {"id": "x", "question": "Curry 2015-16 points?", "expected_tools": ["get_player_stats"],
            "answer_contains": ["30.1"]}
    calls = [{"name": "get_player_stats", "args": {}}]
    answer = "<think>maybe 99.9?</think>Curry averaged 30.1 points."
    result = score_case(case, answer, calls, TOOL_OUT)
    assert result["passed"], result["problems"]
    assert result["checks"] == {"tools": True, "contains": True, "grounded": True}
