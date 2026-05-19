import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_dir: Path
    db_path: Path
    papers_cache_dir: Path
    chroma_dir: Path
    router_model: str
    chitchat_model: str
    paper_qa_model: str
    embedding_model: str
    reranker_model: str
    log_level: str
    # Model-server (sentence-transformers + cross-encoder hosted in a
    # SEPARATE process so uvicorn --reload on backend code doesn't
    # reset the ~110 MB embedder + ~80 MB reranker weights). The backend
    # lifespan auto-spawns this process and terminates it on shutdown.
    model_server_host: str
    model_server_port: int
    # When True, skip the HTTP client + auto-spawn and load models in
    # the worker process directly. Useful for tests (no network round-
    # trip, no port conflicts) and for environments where the operator
    # can't spawn an extra process.
    inprocess_models: bool


def load_settings() -> Settings:
    workspace = Path(os.environ.get("PAPERHUB_WORKSPACE", "./workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        workspace_dir=workspace,
        db_path=workspace / "paperhub.db",
        papers_cache_dir=workspace / "papers_cache",
        chroma_dir=workspace / "chroma",
        router_model=os.environ.get("PAPERHUB_ROUTER_MODEL", "gemini/gemini-2.5-flash"),
        chitchat_model=os.environ.get("PAPERHUB_CHITCHAT_MODEL", "gemini/gemini-2.5-flash"),
        paper_qa_model=os.environ.get("PAPERHUB_PAPER_QA_MODEL", "gemini/gemini-2.5-pro"),
        embedding_model=os.environ.get("PAPERHUB_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        reranker_model=os.environ.get(
            "PAPERHUB_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        ),
        log_level=os.environ.get("PAPERHUB_LOG_LEVEL", "INFO"),
        model_server_host=os.environ.get(
            "PAPERHUB_MODEL_SERVER_HOST", "127.0.0.1",
        ),
        model_server_port=int(
            os.environ.get("PAPERHUB_MODEL_SERVER_PORT", "8001"),
        ),
        inprocess_models=os.environ.get(
            "PAPERHUB_INPROCESS_MODELS", "0",
        ) not in ("0", "", "false", "False"),
    )
