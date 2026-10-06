"""Switch the kiosk panel from the command line: python -m exo_deck.navcli [next|previous|<name>|<1-9>]"""
import sys

from . import state as st
from .config import Settings

if __name__ == "__main__":
    print(st.set_panel(Settings.from_env().state, sys.argv[1] if len(sys.argv) > 1 else "next"))
