from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    raw = Path(path)
    if raw.is_absolute():
        return raw
    return project_root() / raw


def safe_key(value: str) -> str:
    return "_".join(value.strip().lower().replace("/", " ").replace("\\", " ").split())
