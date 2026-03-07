from __future__ import annotations

import logging
import shutil
import subprocess

LOG = logging.getLogger(__name__)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def require_executable(name: str, hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not found on PATH. {hint}")
    return path


def run_checked(cmd: list[str], dry_run: bool = False) -> None:
    LOG.info("run: %s", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)  # noqa: S603


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    LOG.debug("capture: %s", " ".join(cmd))
    return subprocess.run(  # noqa: S603
        cmd,
        check=True,
        text=True,
        capture_output=True,
    )
