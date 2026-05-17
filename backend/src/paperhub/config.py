import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_dir: Path
    db_path: Path
    router_model: str
    chitchat_model: str
    log_level: str


def load_settings() -> Settings:
    workspace = Path(os.environ.get("PAPERHUB_WORKSPACE", "./workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        workspace_dir=workspace,
        db_path=workspace / "paperhub.db",
        router_model=os.environ.get("PAPERHUB_ROUTER_MODEL", "gemini/gemini-2.5-flash"),
        chitchat_model=os.environ.get("PAPERHUB_CHITCHAT_MODEL", "gemini/gemini-2.5-flash"),
        log_level=os.environ.get("PAPERHUB_LOG_LEVEL", "INFO"),
    )
