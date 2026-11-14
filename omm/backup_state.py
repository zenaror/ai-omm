"""Small durable backup receipt, without URLs, credentials or Git error text."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


def backup_metadata(root: Path) -> Path:
    return root / '.git' if (root / '.git').is_dir() else root / '.omm'


def backup_lock_root(root: Path) -> Path:
    return backup_metadata(root) / 'omm-git-backup'


def read_backup_state(root: Path) -> dict:
    path = backup_metadata(root) / 'omm-backup-status.json'
    try:
        if path.is_symlink() or path.stat().st_size > 32768:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_backup_state(root: Path, **fields) -> dict:
    directory = backup_metadata(root)
    directory.mkdir(parents=True, exist_ok=True)
    state = read_backup_state(root)
    state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.backup-status-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / 'omm-backup-status.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return state
