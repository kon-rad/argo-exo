import pytest
from exo_deck.router import Route, route


@pytest.mark.parametrize("said,expected", [
    ("What's my portfolio?", Route("talk", "What's my portfolio?")),
    ("Have the researcher look into gas price APIs.",
     Route("delegate", "look into gas price APIs.", "researcher")),
    ("ask builder to make a gas tile", Route("delegate", "make a gas tile", "builder")),
    ("Tell the wallet agent to check my approvals",
     Route("delegate", "check my approvals", "wallet")),
    ("Note, call Mira about the deposit", Route("delegate", "call Mira about the deposit", "librarian")),
    ("journal: slept badly, demo day", Route("delegate", "slept badly, demo day", "librarian")),
    ("Build me a tile that shows gas", Route("delegate", "Build me a tile that shows gas", "builder")),
    ("Ask the weather is it raining", Route("talk", "Ask the weather is it raining")),
    ("Notebook prices in Akihabara?", Route("talk", "Notebook prices in Akihabara?")),
    ("   ", Route("empty", "")),
    ("", Route("empty", "")),
])
def test_route(said, expected):
    assert route(said) == expected


def test_whitespace_and_newlines_collapse():
    assert route("have the\nresearcher   find  X") == Route("delegate", "find X", "researcher")


def test_custom_agent_list():
    assert route("ask scout to find memes", agents=("scout",)) == Route("delegate", "find memes", "scout")
