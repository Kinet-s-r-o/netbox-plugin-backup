from pathlib import Path


def receiver_profile_root(root: str | Path, profile_id: int) -> Path:
    base = Path(root).expanduser().resolve()
    return base / f"profile-{int(profile_id)}"


def receiver_inbox_path(
    root: str | Path,
    profile_id: int,
    upload_directory: str,
) -> Path:
    if (
        not upload_directory
        or upload_directory in {".", ".."}
        or "/" in upload_directory
        or "\\" in upload_directory
        or "\x00" in upload_directory
    ):
        raise ValueError("Invalid receiver upload directory.")
    profile_root = receiver_profile_root(root, profile_id)
    result = (profile_root / upload_directory).resolve()
    if result.parent != profile_root:
        raise ValueError("Receiver upload directory escapes the profile root.")
    return result
