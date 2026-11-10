"""Configuration management for attendpy."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


def get_app_dir(ensure_exists: bool = False) -> Path:
    """Return the cross-platform configuration directory for attendpy."""
    env_dir = os.environ.get("ATTENDPY_HOME")
    if env_dir:
        path = Path(env_dir).expanduser()
        if ensure_exists:
            path.mkdir(parents=True, exist_ok=True)
        return path

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")

    app_dir = base / "attendpy"
    if ensure_exists:
        try:
            app_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            app_dir = Path.cwd() / ".attendpy"
            app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


@dataclass
class AppConfig:
    """Global application configuration."""

    initialized: bool = False
    provider: str = "apple"  # "apple", "google", or "hybrid"
    cloud_account: str = "iCloud"
    attended_calendar: str = "Lessons"
    skipped_calendar: str = "Skipped Lessons"
    google_attended_ics: str = ""
    google_skipped_ics: str = ""
    google_credentials_file: str = ""
    lab_pattern: str = r"\b(LABORATORIO|LABORATORY|LAB)\b"
    default_theory_threshold: float = 60.0
    default_lab_threshold: float = 80.0
    default_theory_hours: float = 48.0
    default_lab_hours: float = 24.0
    last_sync_at: str = ""

    @classmethod
    def config_path(cls, custom_dir: Optional[Path] = None, ensure_exists: bool = False) -> Path:
        return (custom_dir or get_app_dir(ensure_exists=ensure_exists)) / "config.json"

    @classmethod
    def db_path(cls, custom_dir: Optional[Path] = None, ensure_exists: bool = False) -> Path:
        return (custom_dir or get_app_dir(ensure_exists=ensure_exists)) / "attendpy.db"

    @classmethod
    def load(cls, custom_dir: Optional[Path] = None) -> AppConfig:
        """Load configuration from disk, returning defaults if missing."""
        path = cls.config_path(custom_dir)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            return cls(**filtered)
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self, custom_dir: Optional[Path] = None) -> Path:
        """Persist configuration to JSON file."""
        path = self.config_path(custom_dir, ensure_exists=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        return path


def reset_all_data(custom_dir: Optional[Path] = None) -> list[Path]:
    """Delete all attendpy configuration, SQLite database, and local cache files.

    Returns the list of removed paths.
    """
    removed: list[Path] = []
    candidate_dirs = [custom_dir] if custom_dir else [get_app_dir(ensure_exists=False), Path.cwd() / ".attendpy"]

    for directory in candidate_dirs:
        if directory is None or not directory.exists():
            continue
        for target in (
            directory / "config.json",
            directory / "attendpy.db",
            directory / "calendars",
        ):
            if target.exists():
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    removed.append(target)
                except OSError:
                    pass
    return removed
