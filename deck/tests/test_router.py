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


@pytest.mark.parametrize("said,expected", [
    ("Show approvals", Route("nav", "approvals")),
    ("go to the wallets panel", Route("nav", "wallets")),
    ("open ring", Route("nav", "body")),
    ("show the camera", Route("nav", "media")),
    ("panel three", Route("nav", "3")),
    ("panel 7", Route("nav", "7")),
    ("next panel", Route("nav", "next")),
    ("previous", Route("nav", "previous")),
    ("more", Route("nav", "more")),
    ("back", Route("nav", "back")),
    ("auto approve off", Route("control", "auto-off")),
    ("Auto-approve on.", Route("control", "auto-on")),
    ("auto approve off please", Route("control", "auto-off")),
    ("turn auto approve off", Route("control", "auto-off")),
    ("switch auto approve off", Route("control", "auto-off")),
    ("Turn auto-approve off, please.", Route("control", "auto-off")),
    ("auto approve off…", Route("control", "auto-off")),
    ("turn auto approve on", Route("control", "auto-on")),
    ("switch auto approve on please", Route("control", "auto-on")),
    ("auto approve on please", Route("control", "auto-on")),
    ("turn off auto approve", Route("control", "auto-off")),
    ("switch off auto approve", Route("control", "auto-off")),
    ("Turn off auto-approve, please.", Route("control", "auto-off")),
    ("please switch off the auto approve", Route("control", "auto-off")),
    ("turn on auto approve", Route("control", "auto-on")),
    ("turn on the lights", Route("talk", "turn on the lights")),
    ("open 4", Route("media-open", "4")),
    ("close", Route("control", "close")),
    ("show me how the guardian works", Route("talk", "show me how the guardian works")),
    ("panel ten", Route("talk", "panel ten")),
    ("open the pod bay doors", Route("talk", "open the pod bay doors")),
])
def test_nav_and_control(said, expected):
    assert route(said) == expected
