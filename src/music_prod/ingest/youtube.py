from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def require_exe(name: str, install_hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found on PATH. {install_hint}")
    return path


def download_audio(
    url: str,
    out_dir: Path,
    *,
    audio_format: str = "mp3",
) -> Path:
    """
    Download audio from YouTube (or similar) using yt-dlp.
    Returns the path to the downloaded file.
    """
    require_exe("yt-dlp", "Install: pip install -U yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)

    output_template = out_dir / "%(title)s.%(ext)s"

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", audio_format,
        "-o", str(output_template),
        url,
    ]

    subprocess.run(cmd, check=True)

    files = list(out_dir.glob(f"*.{audio_format}"))
    if not files:
        raise RuntimeError("yt-dlp completed but no audio file was found")

    return files[0]
