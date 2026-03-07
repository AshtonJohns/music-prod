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
- watchdog

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

## Camera + Audio File Sync Capture

Use this when an external recorder writes an audio file and you want Python to:
1. start camera capture immediately (pre-roll),
2. wait for the audio file trigger,
3. stop capture when the audio file is unchanged for `N` seconds,
4. trim pre-roll from the recorded camera video.

### Install/refresh deps

```powershell
uv sync --extra dev
```

### List camera devices (Windows DirectShow)

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

### Run sync capture (Windows example)

```powershell
uv run music-prod-sync-capture `
  --audio-file ".\recordings\session.wav" `
  --input-format dshow `
  --input-device "video=USB Camera" `
  --output-dir ".\capture_out" `
  --inactivity-seconds 5 `
  --poll-seconds 0.5
```

### Run sync capture from a Cakewalk project Audio folder

Wait for a new `.wav` file to appear in `Audio`, then auto-save output to `Video` next to it:

```powershell
uv run music-prod-sync-capture `
  --cwp-audio ".\projects\my_song-ashton\Audio" `
  --input-format dshow `
  --input-device "video=USB Camera" `
  --inactivity-seconds 5 `
  --poll-seconds 0.5
```

### Important config knobs

- Manual stop: press `Ctrl+C` to stop recording. If audio trigger was already detected, trim still runs.
- `--wait-for-audio-timeout`: max wait for file creation (`0` = no timeout)
- `--cwp-audio`: watch a Cakewalk `Audio` folder for a new `.wav` to trigger recording
- `--inactivity-seconds`: stop threshold for unchanged file size + mtime
- `--poll-seconds`: check interval
- `--max-record-seconds`: fail-safe max duration (`0` = disabled)
- `--output-dir`: defaults to `capture_out`, or to `<cwp parent>/Video` when `--cwp-audio` is used
- `--input-format`: `dshow` (Windows), `avfoundation` (macOS), `v4l2` (Linux)
- `--input-device`: ffmpeg device string for your camera
- `--framerate`, `--video-size`, `--video-codec`, `--preset`, `--crf`: capture quality/performance
- `--video-pixel-format`, `--video-profile`, `--video-level`: compatibility controls for capture output (defaults: `yuv420p`, `high`, `4.1`)
- `--skip-mux`: only keep raw camera video (skip final trim output)

## Simple Camera Video Capture (Video + Webcam Audio)

Use this when you just want one raw camera file with embedded webcam audio:

```powershell
uv run music-prod-cakewalk-video `
  --input-device "video=c922 Pro Stream Webcam" `
  --output-file ".\projects\the_living_dead-ashton\Video\camera_raw.mp4"
```

If your webcam audio device name is different, set it explicitly:

```powershell
uv run music-prod-cakewalk-video `
  --input-device "video=c922 Pro Stream Webcam" `
  --audio-device "c922 Pro Stream Webcam"
```

## Sync Camera Video With Cakewalk WAV

This command detects the offset between the MP4 embedded webcam audio and an external WAV,
then writes a corrected MP4 while leaving the WAV untouched.

Behavior:
- If MP4 leads WAV, MP4 start is trimmed.
- If MP4 lags WAV, black video + silence are prepended to the MP4.
- The MP4 keeps its original embedded audio content (shifted with the video timeline).
- The WAV is never modified.

```powershell
uv run music-prod-video-audio-sync `
  --video ".\projects\the_living_dead-ashton\Video\camera_raw.mp4" `
  --audio ".\projects\the_living_dead-ashton\Audio Export\the_living_dead-ashton.wav" `
  --output ".\projects\the_living_dead-ashton\Video\synced_output.mp4"
```

Important options:

- `--report-json` writes a sidecar JSON report (default: `<output>.sync-report.json`)
- `--sample-rate` analysis rate (default `16000`)
- `--analysis-seconds` analyze only first N seconds
- `--max-offset-seconds` lag search window
- `--min-confidence` warns on low-confidence matches
- `--dry-run` prints ffmpeg actions without writing corrected media
- `--force` allows overwriting existing output


## Pipeline for New Projects

### Open Cakewalk 

➡️ New / Save As 

➡️ Navigate to `./projects`

➡️ Create project with file name with snake-case for song and hyphentated name of singer. E.g., song_name-bob. Ensure __**Project Path**__ and __**Audio Path**__ are set correctly! The file type should be *Normal*.

```
Project Path:  ./projects/{SONG_NAME-SINGER}
Audio Path:  ./projects/{SONG_NAME-SINGER}/Audio

```

### Open music-prod workspace and run these scripts...

➡️ music-prod.exe

```
music-prod.exe `
  "{link}"
```

➡️ music-prod-cakewalk-setup.exe

```
music-prod-cakewalk-setup.exe `
  --stems-dir="./trascribe_out/10_stems/htdemucs/{htdemucs-generated-name}" `
  --song-name="{SONG_NAME}"
```

#### Note that {SONG_NAME} matches the Cakewalk project!

➡️ music-prod-cakewalk-video.exe

#### Before the song starts

Make a visible and audible sync cue near the beginning of the take.

- Clap once on camera
- Or play a short beep/count-in into the room
- Or say a slate and clap

```
music-prod-cakewalk-video `
  --output-file "./projects/{SONG_NAME-SINGER}/Video/camera_raw.mp4" `
  --input-device "video=c922 Pro Stream Webcam" `
  --audio-device "Microphone (C922 Pro Stream Webcam)"
```


➡️ music-prod-video-audio-sync.exe


This cue is what the sync tool should align to. Do not rely on passive waveform matching as the primary workflow.

```
music-prod-video-audio-sync.exe `
  --video "C:\Users\ashto\Desktop\music-prod\projects\the_living_dead-ashton\Video\camera_raw.mp4" `
  --audio "C:\Users\ashto\Desktop\music-prod\projects\the_living_dead-ashton\Audio\the_living_dead-ashton, Lalala, Rec (143).wav" `
  --output "C:\Users\ashto\Desktop\music-prod\projects\the_living_dead-ashton\Video\synced_output.mp4" `
  --sample-rate 16000
```

<!-- #### Output expectations

- The tool prints detected offset, confidence, and selected correction operation
- A JSON report is written with offset, confidence, method, operation, and output path
- If stream-copy trim is unsafe (trim point not keyframe), ffmpeg falls back to re-encode and logs why
- Cakewalk audio remains production source and is never rewritten in place -->



