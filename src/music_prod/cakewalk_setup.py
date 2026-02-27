from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def slugify_name(raw: str) -> str:
    """Convert arbitrary text into a filesystem-friendly project name."""
    collapsed = re.sub(r"\s+", " ", raw.strip())
    slug = re.sub(r"[^A-Za-z0-9._ -]", "", collapsed)
    slug = slug.replace(" ", "_")
    return slug or "new_song"


def _copy_file(source: Path, destination: Path) -> None:
    destination.write_bytes(source.read_bytes())


def _copy_template(
    *,
    template: Path | None,
    project_dir: Path,
    template_dir: Path,
    project_name: str,
) -> tuple[Path | None, Path | None]:
    copied_template: Path | None = None
    project_file: Path | None = None

    if template is None:
        return copied_template, project_file

    template_path = template.expanduser().resolve()
    if not template_path.exists() or not template_path.is_file():
        raise FileNotFoundError(f"Template not found: {template_path}")

    suffix = template_path.suffix.lower()
    if suffix == ".cwp":
        project_file = project_dir / f"{project_name}.cwp"
        _copy_file(template_path, project_file)
        copied_template = project_file
    else:
        copied_template = template_dir / template_path.name
        _copy_file(template_path, copied_template)

    return copied_template, project_file


def _build_summary(
    *,
    project_name: str,
    project_dir: Path,
    copied_template: Path | None,
    project_file: Path | None,
    instrumental_source: Path | None = None,
    instrumental_copy: Path | None = None,
    stems_source_dir: Path | None = None,
    stems_copied: list[Path] | None = None,
) -> dict[str, object]:
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_name": project_name,
        "project_dir": str(project_dir),
        "instrumental_source": str(instrumental_source) if instrumental_source else "",
        "instrumental_copy": str(instrumental_copy) if instrumental_copy else "",
        "stems_source_dir": str(stems_source_dir) if stems_source_dir else "",
        "stems_copied": [str(path) for path in (stems_copied or [])],
        "template_copy": str(copied_template) if copied_template else "",
        "project_file": str(project_file) if project_file else "",
        "next_step": (
            "Open Cakewalk, load template (if .cwt), then Save As into this project folder."
        ),
    }


def setup_cakewalk_project(
    instrumental: Path,
    projects_root: Path,
    *,
    template: Path | None = None,
    song_name: str | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Create a new Cakewalk-ready project folder from an instrumental and optional template."""
    source = instrumental.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Instrumental not found: {source}")

    project_name = slugify_name(song_name or source.stem)
    root = projects_root.expanduser().resolve()
    project_dir = root / project_name
    audio_dir = project_dir / "Audio"
    template_dir = project_dir / "Template"

    if project_dir.exists() and not force:
        raise FileExistsError(
            f"Project already exists: {project_dir}. Use --force to reuse this folder."
        )

    audio_dir.mkdir(parents=True, exist_ok=True)
    template_dir.mkdir(parents=True, exist_ok=True)

    copied_audio = audio_dir / source.name
    _copy_file(source, copied_audio)
    copied_template, project_file = _copy_template(
        template=template,
        project_dir=project_dir,
        template_dir=template_dir,
        project_name=project_name,
    )
    summary = _build_summary(
        project_name=project_name,
        project_dir=project_dir,
        copied_template=copied_template,
        project_file=project_file,
        instrumental_source=source,
        instrumental_copy=copied_audio,
    )

    metadata_path = project_dir / "project_setup.json"
    metadata_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def setup_cakewalk_project_from_stems(
    stems_dir: Path,
    projects_root: Path,
    *,
    template: Path | None = None,
    song_name: str | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Create a Cakewalk-ready project folder by copying all .wav stems from a folder."""
    source_dir = stems_dir.expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"Stems directory not found: {source_dir}")

    wav_files = sorted(
        [path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".wav"]
    )
    if not wav_files:
        raise ValueError(f"No .wav files found in stems directory: {source_dir}")

    project_name = slugify_name(song_name or source_dir.name)
    root = projects_root.expanduser().resolve()
    project_dir = root / project_name
    audio_dir = project_dir / "Audio"
    template_dir = project_dir / "Template"

    if project_dir.exists() and not force:
        raise FileExistsError(
            f"Project already exists: {project_dir}. Use --force to reuse this folder."
        )

    audio_dir.mkdir(parents=True, exist_ok=True)
    template_dir.mkdir(parents=True, exist_ok=True)

    copied_stems: list[Path] = []
    for wav_file in wav_files:
        destination = audio_dir / wav_file.name
        _copy_file(wav_file, destination)
        copied_stems.append(destination)

    copied_template, project_file = _copy_template(
        template=template,
        project_dir=project_dir,
        template_dir=template_dir,
        project_name=project_name,
    )
    summary = _build_summary(
        project_name=project_name,
        project_dir=project_dir,
        copied_template=copied_template,
        project_file=project_file,
        stems_source_dir=source_dir,
        stems_copied=copied_stems,
    )

    metadata_path = project_dir / "project_setup.json"
    metadata_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Cakewalk project workspace from an instrumental and optional template."
        )
    )
    parser.add_argument(
        "instrumental",
        nargs="?",
        help="Path to instrumental audio file (single-file mode)",
    )
    parser.add_argument(
        "--stems-dir",
        help="Path to a folder containing stem .wav files (stems mode)",
    )
    parser.add_argument(
        "--projects-root",
        default="projects",
        help="Root folder where song projects are created (default: projects)",
    )
    parser.add_argument("--template", help="Optional Cakewalk template/project file (.cwt or .cwp)")
    parser.add_argument("--song-name", help="Override project folder name")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow reusing an existing project folder",
    )
    args = parser.parse_args()

    if bool(args.instrumental) == bool(args.stems_dir):
        parser.error("Provide exactly one input: positional 'instrumental' OR '--stems-dir'.")

    if args.stems_dir:
        result = setup_cakewalk_project_from_stems(
            Path(args.stems_dir),
            Path(args.projects_root),
            template=Path(args.template) if args.template else None,
            song_name=args.song_name,
            force=args.force,
        )
    else:
        result = setup_cakewalk_project(
            Path(args.instrumental),
            Path(args.projects_root),
            template=Path(args.template) if args.template else None,
            song_name=args.song_name,
            force=args.force,
        )

    print("Created project:", result["project_dir"])
    if result["instrumental_copy"]:
        print("Audio copied to:", result["instrumental_copy"])
    if result["stems_copied"]:
        print("Stems copied:", len(result["stems_copied"]))
    if result["template_copy"]:
        print("Template copied to:", result["template_copy"])
    print("Metadata:", Path(result["project_dir"]) / "project_setup.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
