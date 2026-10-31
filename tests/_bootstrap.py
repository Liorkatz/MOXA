"""Resolve the application root and keep generated images out of it."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCREENSHOTS = ROOT / 'archive' / 'screenshots'
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
