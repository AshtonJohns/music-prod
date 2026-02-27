## System Requirements

- ffmpeg
  - Windows: `winget install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Linux: `apt install ffmpeg`

## Python Tools

Installed via `pyproject.toml`:
- yt-dlp
- demucs
- basic-pitch

## Cakewalk Workspace Bootstrap

Use a template-first flow and let the script create song folders + copy assets:

```powershell
uv sync --extra dev
uv run music-prod-cakewalk-setup ".\my_song_instrumental.mp3" --template ".\templates\blank.cwt" --song-name "My New Song"
```

This creates:
- `projects/<SongName>/Audio/<instrumental file>`
- `projects/<SongName>/Template/<template file>` (for `.cwt`) or `projects/<SongName>/<SongName>.cwp` (for `.cwp`)
- `projects/<SongName>/project_setup.json`

## Local Quality Checks (No CI Required)

```powershell
uv run ruff check .
uv run ruff format .
uv run pytest
```

Recommended for this repo:
- `pytest`: yes
- `ruff`: yes
- `pyright`: skip for now
