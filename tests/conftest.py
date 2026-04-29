"""Tests import core modules (protocol, hub, state) which don't depend on HA."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
