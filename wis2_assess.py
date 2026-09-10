#!/usr/bin/env python3
"""WIS2 Node Assessment console.

Subscribe to origin, cache and monitor topics on a WIS2 Global Broker,
inspect notifications in a three-pane MQTT Explorer-style terminal UI,
and write a GISC assessment report from the captured messages.

Install:
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

Run:
    python3 wis2_assess.py
    python3 wis2_assess.py --centre <centre-id>
"""

from __future__ import annotations

from wis2_assessment.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
