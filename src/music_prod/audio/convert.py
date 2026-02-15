import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install via:\n"
            "  winget install ffmpeg"
        )
    return path


def to_wav(input_file: Path, output_file: Path, *, sr: int = 44100) -> None:
    require_ffmpeg()

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_file),
        "-ar", str(sr),
        str(output_file),
    ]
    subprocess.run(cmd, check=True)
