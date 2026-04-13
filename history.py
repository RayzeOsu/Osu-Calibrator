import os
import json
import shutil
import tempfile
from dataclasses import asdict
from typing import List, Optional

from config import (
    HISTORY_SCHEMA_VERSION,
    APP_NAME,
    MAX_HISTORY_ENTRIES,
    HISTORY_FILE,
    HISTORY_BACKUP,
)
from models import CalibrationSession

class HistoryStore:
    def __init__(self, path: str = HISTORY_FILE, backup_path: str = HISTORY_BACKUP):
        self.path = path
        self.backup_path = backup_path
        self.sessions: List[CalibrationSession] = []
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and "schema_version" in data:
                file_version = data.get("schema_version", 0)
                sessions_data = data.get("sessions", [])
                if file_version > HISTORY_SCHEMA_VERSION:
                    print(f"History file is newer version ({file_version}) than this app supports ({HISTORY_SCHEMA_VERSION}). Loading read-only.")
            elif isinstance(data, list):
                sessions_data = data
            else:
                raise ValueError("History file has unknown structure")

            self.sessions = []
            for item in sessions_data:
                if not isinstance(item, dict):
                    continue
                self.sessions.append(
                    CalibrationSession(
                        settings=item.get("settings", {}),
                        summary=item.get("summary", {}),
                    )
                )
        except (json.JSONDecodeError, ValueError, OSError) as e:
            print(f"History file corrupted ({e}). Backing up.")
            try:
                shutil.copy2(self.path, self.backup_path)
            except OSError:
                pass
            self.sessions = []

    def save(self) -> None:
        try:
            recent = self.sessions[-MAX_HISTORY_ENTRIES:]
            payload = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "app_name": APP_NAME,
                "sessions": [asdict(s) for s in recent],
            }

            dir_name = os.path.dirname(os.path.abspath(self.path)) or "."
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=dir_name,
                suffix=".tmp",
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name

            os.replace(tmp_path, self.path)
        except OSError as e:
            print(f"Failed to save history: {e}")

    def append(self, session: CalibrationSession) -> None:
        self.sessions.append(session)
        self.save()

    def clear(self) -> None:
        self.sessions = []
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError as e:
            print(f"Failed to delete history file: {e}")

    def latest(self) -> Optional[CalibrationSession]:
        return self.sessions[-1] if self.sessions else None

    def previous(self) -> Optional[CalibrationSession]:
        return self.sessions[-2] if len(self.sessions) >= 2 else None