"""Run: uv run --directory marker_service --with pytest pytest"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
