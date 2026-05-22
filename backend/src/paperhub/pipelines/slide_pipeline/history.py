# Adapted from reference/paper2slides-plus/src/history.py @ 88515c4
# Original project: https://github.com/whats2000/paper2slides-plus (MIT).
"""
Version history management for Paper2Slides.
Saves working versions after successful PDF compilations.
Allows reverting to previous working versions when LLM edits fail.

Each version snapshot bundles both the LaTeX source and any associated
speaker_notes.json so restoring a version keeps the two artifacts in
lockstep — without this, restoring an older deck would leave today's
speaker notes attached to the wrong slide numbers.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path


class VersionHistory:
    """Manages version history for a paper's slides - saves only after successful compiles."""

    def __init__(self, workspace_dir: str | Path) -> None:
        """
        Initialize version history for a specific workspace directory.

        Args:
            workspace_dir: Workspace directory path for the slides
        """
        self.workspace_dir = Path(workspace_dir)
        self.history_dir = self.workspace_dir / "edit_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _speaker_notes_path(self) -> Path:
        return self.workspace_dir / "speaker_notes.json"

    def _load_workspace_speaker_notes(self) -> dict[str, str] | None:
        """Load the current speaker_notes.json, or None if missing/unreadable.

        Returned dict uses string keys (matching the on-disk JSON shape) so it
        round-trips through json.dump cleanly.
        """
        path = self._speaker_notes_path
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Failed to read speaker_notes.json for snapshot: {e}")
            return None

    def save_version(
        self,
        tex_content: str,
        description: str = "Successful compile",
        speaker_notes: dict[str, str] | None = None,
    ) -> bool:
        """
        Save a new version after successful compilation.

        Args:
            tex_content: The LaTeX content to save
            description: Description of this version
            speaker_notes: Optional speaker notes dict to bundle with this version.
                If None, the current speaker_notes.json on disk (if any) is captured
                automatically so callers don't have to thread the data through.

        Returns:
            True if successful, False otherwise
        """
        try:
            timestamp = datetime.now()
            # Use timestamp as filename for easy sorting
            timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

            if speaker_notes is None:
                snapshot_notes = self._load_workspace_speaker_notes()
            else:
                snapshot_notes = {str(k): v for k, v in speaker_notes.items()}

            version_data = {
                "timestamp": timestamp.isoformat(),
                "description": description,
                "tex_content": tex_content,
                "speaker_notes": snapshot_notes,  # may be None for decks without notes yet
            }

            version_file = self.history_dir / f"version_{timestamp_str}.json"
            with open(version_file, "w", encoding="utf-8") as f:
                json.dump(version_data, f, indent=2, ensure_ascii=False)

            note_count = len(snapshot_notes) if snapshot_notes else 0
            logging.info(
                f"Saved version: {description} at {timestamp_str} "
                f"(speaker notes bundled: {note_count})"
            )
            return True

        except Exception as e:
            logging.error(f"Failed to save version: {e}")
            return False

    def list_versions(self) -> list[dict[str, str]]:
        """
        List all saved versions, newest first.

        Returns:
            List of version dictionaries with metadata (without full tex_content)
        """
        versions: list[dict[str, str]] = []
        if not self.history_dir.exists():
            return versions

        # Read all version files and sort by recorded timestamp (newest first)
        entries: list[tuple[str, datetime | None, str]] = []
        for version_file in self.history_dir.glob("version_*.json"):
            try:
                with open(version_file, encoding="utf-8") as f:
                    data = json.load(f)
                    ts = data.get("timestamp")
                    # Try to parse timestamp to datetime for reliable sorting
                    try:
                        parsed_ts: datetime | None = (
                            datetime.fromisoformat(ts) if ts else None
                        )
                    except Exception:
                        parsed_ts = None

                    entries.append(
                        (
                            version_file.name,
                            parsed_ts,
                            data.get("description", "Unknown"),
                        )
                    )
            except Exception as e:
                logging.error(f"Failed to read version file {version_file}: {e}")

        # Sort by parsed timestamp descending (None values go last)
        entries.sort(key=lambda x: x[1] or datetime.min, reverse=True)

        for filename, parsed_ts, desc in entries:
            versions.append(
                {
                    "filename": filename,
                    "timestamp": parsed_ts.isoformat() if parsed_ts else "",
                    "description": desc,
                }
            )

        return versions

    def get_latest_version(self) -> str | None:
        """
        Get the most recent working version.

        Returns:
            LaTeX content of the latest version, or None if no history exists
        """
        versions = self.list_versions()
        if not versions:
            return None

        latest_file = self.history_dir / versions[0]["filename"]
        try:
            with open(latest_file, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tex_content")  # type: ignore[no-any-return]
        except Exception as e:
            logging.error(f"Failed to read latest version: {e}")
            return None

    def get_version_by_filename(self, filename: str) -> str | None:
        """
        Get a specific version by filename.

        Args:
            filename: The version filename (e.g., "version_20250107_143022.json")

        Returns:
            LaTeX content if found, None otherwise
        """
        version_file = self.history_dir / filename
        if not version_file.exists():
            return None

        try:
            with open(version_file, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tex_content")  # type: ignore[no-any-return]
        except Exception as e:
            logging.error(f"Failed to read version {filename}: {e}")
            return None

    def restore_version(self, filename: str, slides_tex_path: str) -> bool:
        """
        Restore a specific version to slides.tex AND its bundled speaker_notes.json.

        Restoration semantics:
          - tex_content is always written back to slides.tex.
          - If the version has a 'speaker_notes' field (new format):
              * non-null dict -> overwrite speaker_notes.json with the snapshot
              * null         -> remove speaker_notes.json (snapshot recorded "no notes")
          - If the version has no 'speaker_notes' field (legacy snapshot from before
            this feature existed): leave the current speaker_notes.json alone — we
            don't know what was paired with that older deck, and silently wiping
            user-edited notes would be a worse failure than a stale-but-present file.

        Args:
            filename: The version filename to restore
            slides_tex_path: Path to slides.tex file

        Returns:
            True if successful, False otherwise
        """
        version_file = self.history_dir / filename
        if not version_file.exists():
            logging.error(f"Version {filename} not found")
            return False

        try:
            with open(version_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read version {filename}: {e}")
            return False

        tex_content = data.get("tex_content")
        if tex_content is None:
            logging.error(f"Version {filename} has no tex_content")
            return False

        try:
            with open(slides_tex_path, "w", encoding="utf-8") as f:
                f.write(tex_content)
            logging.info(f"Restored slides.tex from version {filename}")
        except Exception as e:
            logging.error(f"Failed to restore slides.tex: {e}")
            return False

        if "speaker_notes" in data:
            snapshot_notes = data["speaker_notes"]
            try:
                if snapshot_notes:
                    with self._speaker_notes_path.open("w", encoding="utf-8") as f:
                        json.dump(snapshot_notes, f, indent=2, ensure_ascii=False)
                    logging.info(
                        f"Restored speaker_notes.json from version {filename} "
                        f"({len(snapshot_notes)} entries)"
                    )
                else:
                    if self._speaker_notes_path.exists():
                        self._speaker_notes_path.unlink()
                        logging.info(
                            f"Removed speaker_notes.json (version {filename} had none bundled)"
                        )
            except Exception as e:
                logging.error(f"Failed to restore speaker_notes.json: {e}")
                return False
        else:
            logging.debug(
                f"Version {filename} predates speaker-note tracking; leaving "
                "current speaker_notes.json untouched."
            )

        return True

    def delete_version(self, filename: str) -> bool:
        """
        Delete a specific saved version file.

        Args:
            filename: The version filename to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        version_file = self.history_dir / filename
        if not version_file.exists():
            logging.warning(f"Version file {filename} does not exist")
            return False

        try:
            version_file.unlink()
            logging.info(f"Deleted history file {filename}")
            return True
        except Exception as e:
            logging.error(f"Failed to delete version {filename}: {e}")
            return False

    def has_history(self) -> bool:
        """Check if any version history exists."""
        return len(self.list_versions()) > 0

    def clear_history(self, preserve_current: str | None = None) -> bool:
        """
        Clear all version history, preserving the initial version and optionally the current active version.

        Args:
            preserve_current: Filename of the currently active version to preserve (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.history_dir.exists():
                return True

            # Get all versions to identify versions to preserve
            versions = self.list_versions()
            versions_to_preserve = []

            # Find the initial version (oldest with "Initial version" description)
            if versions:
                oldest_version = versions[-1]
                if oldest_version["description"].startswith("Initial version"):
                    versions_to_preserve.append(oldest_version["filename"])

            # Add current version if specified and different from initial
            if preserve_current and preserve_current not in versions_to_preserve:
                versions_to_preserve.append(preserve_current)

            # Read content of versions to preserve
            preserved_data: dict[str, object] = {}
            for filename in versions_to_preserve:
                version_file = self.history_dir / filename
                if version_file.exists():
                    try:
                        with open(version_file, encoding="utf-8") as f:
                            preserved_data[filename] = json.load(f)
                    except Exception as e:
                        logging.error(
                            f"Failed to read version to preserve {filename}: {e}"
                        )

            # Clear the directory
            shutil.rmtree(self.history_dir)
            self.history_dir.mkdir(parents=True, exist_ok=True)

            # Restore preserved versions
            for filename, data in preserved_data.items():
                version_file = self.history_dir / filename
                try:
                    with open(version_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception as e:
                    logging.error(
                        f"Failed to restore preserved version {filename}: {e}"
                    )

            preserved_count = len(preserved_data)
            logging.info(
                f"Cleared history, preserved {preserved_count} version(s)"
            )
            return True
        except Exception as e:
            logging.error(f"Failed to clear history: {e}")
            return False


def get_history_manager(workspace_dir: str | Path) -> VersionHistory:
    """
    Get a version history manager for a specific workspace directory.

    Args:
        workspace_dir: Workspace directory path for the slides

    Returns:
        VersionHistory instance
    """
    return VersionHistory(workspace_dir)
