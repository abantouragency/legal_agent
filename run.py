"""Entry point used by Render (and locally) to launch the bot.

Render runs from the repo root, so importing the package-style src.bot via
`python -m src.bot` works, but a plain `python run.py` is the most robust
launcher (no module-path surprises across platforms).
"""
import sys
import os

# Make sure the repo root (this file's directory) is importable.
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import bot

if __name__ == "__main__":
    bot.main()
