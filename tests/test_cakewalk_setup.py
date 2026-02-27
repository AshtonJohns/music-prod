from pathlib import Path

import pytest

from music_prod.cakewalk_setup import (
    setup_cakewalk_project,
    setup_cakewalk_project_from_stems,
    slugify_name,
)


def test_slugify_name_removes_unsafe_chars() -> None:
    assert slugify_name(" My Song: v1? ") == "My_Song_v1"


def test_setup_creates_project_and_copies_audio_and_cwp(tmp_path: Path) -> None:
    instrumental = tmp_path / "beat.mp3"
    instrumental.write_bytes(b"fake-audio")

    template = tmp_path / "blank.cwp"
    template.write_bytes(b"fake-project")

    result = setup_cakewalk_project(
        instrumental,
        tmp_path / "projects",
        template=template,
        song_name="Demo Song",
    )

    project_dir = Path(result["project_dir"])
    assert project_dir.exists()
    assert (project_dir / "Audio" / "beat.mp3").read_bytes() == b"fake-audio"
    assert (project_dir / "Demo_Song.cwp").read_bytes() == b"fake-project"
    assert (project_dir / "project_setup.json").exists()


def test_setup_copies_cwt_into_template_folder(tmp_path: Path) -> None:
    instrumental = tmp_path / "beat.wav"
    instrumental.write_bytes(b"fake-audio")

    template = tmp_path / "vocal_chain.cwt"
    template.write_bytes(b"fake-template")

    result = setup_cakewalk_project(instrumental, tmp_path / "projects", template=template)

    project_dir = Path(result["project_dir"])
    copied = project_dir / "Template" / "vocal_chain.cwt"
    assert copied.read_bytes() == b"fake-template"


def test_existing_project_raises_without_force(tmp_path: Path) -> None:
    instrumental = tmp_path / "beat.wav"
    instrumental.write_bytes(b"fake-audio")

    setup_cakewalk_project(instrumental, tmp_path / "projects", song_name="Song")

    with pytest.raises(FileExistsError):
        setup_cakewalk_project(instrumental, tmp_path / "projects", song_name="Song")


def test_setup_from_stems_copies_all_wav_files(tmp_path: Path) -> None:
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir(parents=True)
    (stems_dir / "bass.wav").write_bytes(b"bass")
    (stems_dir / "drums.wav").write_bytes(b"drums")
    (stems_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    result = setup_cakewalk_project_from_stems(
        stems_dir,
        tmp_path / "projects",
        song_name="Demo Stems",
    )

    project_dir = Path(result["project_dir"])
    assert (project_dir / "Audio" / "bass.wav").read_bytes() == b"bass"
    assert (project_dir / "Audio" / "drums.wav").read_bytes() == b"drums"
    assert not (project_dir / "Audio" / "notes.txt").exists()
    assert len(result["stems_copied"]) == 2


def test_setup_from_stems_requires_at_least_one_wav(tmp_path: Path) -> None:
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir(parents=True)
    (stems_dir / "readme.txt").write_text("no wav", encoding="utf-8")

    with pytest.raises(ValueError, match="No .wav files found"):
        setup_cakewalk_project_from_stems(stems_dir, tmp_path / "projects")
