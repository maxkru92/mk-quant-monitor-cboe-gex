"""Marker — makes cboe_menthorq_dashboard importable as a package.

Lets tools, IDEs, and tests reference the dashboard via absolute package
imports (e.g. ``from cboe_menthorq_dashboard.ui.chrome import render_market_clock``)
without depending on the launch cwd. The Streamlit Cloud runtime still works
fine via relative ``from ui…`` imports when it asserts cwd to this directory;
this empty file just adds a belt-and-braces safety net for any custom launch
script, Replit-style runner, or unit-test scaffolding.
"""
