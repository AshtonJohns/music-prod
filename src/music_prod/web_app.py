from __future__ import annotations

import asyncio
import html
import os
import shlex
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote_plus

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="music-prod web runner")


@dataclass
class Job:
    id: str
    tool: str
    command: list[str]
    status: str = "queued"
    return_code: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: str | None = None
    finished_at: str | None = None
    output: list[str] = field(default_factory=list)


JOBS: dict[str, Job] = {}
RUNNING_PROCS: dict[str, asyncio.subprocess.Process] = {}
JOBS_LOCK = asyncio.Lock()
MAX_LOG_LINES = 400


def _value(form: dict[str, str], key: str, default: str = "") -> str:
    return str(form.get(key, default)).strip()


def _checked(form: dict[str, str], key: str) -> bool:
    return key in form


def _add_opt(args: list[str], flag: str, value: str) -> None:
    if value:
        args.extend([flag, value])


def _build_transcribe(form: dict[str, str]) -> list[str]:
    source = _value(form, "input")
    if not source:
        raise ValueError("Transcribe pipeline requires an input URL or file path.")

    args = [source]
    _add_opt(args, "--workdir", _value(form, "workdir", "transcribe_out"))
    _add_opt(args, "--model", _value(form, "model", "htdemucs"))
    _add_opt(args, "--device", _value(form, "device", "auto"))
    _add_opt(args, "--stems", _value(form, "stems", "vocals,bass,other"))

    if _checked(form, "analyze_key"):
        args.append("--analyze-key")
        _add_opt(args, "--key-source", _value(form, "key_source", "midi"))
        _add_opt(args, "--key-stems", _value(form, "key_stems", "bass,other"))

    return args


def _build_cakewalk_setup(form: dict[str, str]) -> list[str]:
    mode = _value(form, "input_mode", "instrumental")
    args: list[str] = []
    instrumental = _value(form, "instrumental")
    stems_dir = _value(form, "stems_dir")

    # UX guardrail: if mode says instrumental but only stems path is provided, switch.
    if mode == "instrumental" and not instrumental and stems_dir:
        mode = "stems"

    if mode == "stems":
        if not stems_dir:
            raise ValueError("Cakewalk setup stems mode requires --stems-dir.")
        args.extend(["--stems-dir", stems_dir])
    else:
        if not instrumental:
            raise ValueError("Cakewalk setup instrumental mode requires an input file path.")
        args.append(instrumental)

    _add_opt(args, "--projects-root", _value(form, "projects_root", "projects"))
    _add_opt(args, "--template", _value(form, "template"))
    _add_opt(args, "--song-name", _value(form, "song_name"))
    _add_opt(args, "--lead-in-seconds", _value(form, "lead_in_seconds", "10"))
    if _checked(form, "force"):
        args.append("--force")
    return args


def _build_cakewalk_video(form: dict[str, str]) -> list[str]:
    output_file = _value(form, "output_file")
    input_device = _value(form, "input_device", "video=c922 Pro Stream Webcam")
    if not output_file or not input_device:
        raise ValueError("Cakewalk video requires output file and input device.")

    args = ["--output-file", output_file, "--input-device", input_device]
    _add_opt(args, "--audio-device", _value(form, "audio_device"))
    _add_opt(args, "--input-format", _value(form, "input_format", "dshow"))
    return args


def _build_sync_capture(form: dict[str, str]) -> list[str]:
    args: list[str] = []
    audio_file = _value(form, "audio_file")
    cwp_audio = _value(form, "cwp_audio")
    if audio_file and cwp_audio:
        raise ValueError("Use either audio file or cwp audio directory, not both.")
    if not audio_file and not cwp_audio:
        raise ValueError("Sync capture requires an audio file or cwp audio directory.")

    if audio_file:
        args.extend(["--audio-file", audio_file])
    else:
        args.extend(["--cwp-audio", cwp_audio])

    input_device = _value(form, "input_device")
    if not input_device:
        raise ValueError("Sync capture requires input device.")

    args.extend(["--input-device", input_device])
    _add_opt(args, "--input-format", _value(form, "input_format", "dshow"))
    _add_opt(args, "--output-dir", _value(form, "output_dir"))
    _add_opt(args, "--inactivity-seconds", _value(form, "inactivity_seconds", "5"))
    _add_opt(args, "--poll-seconds", _value(form, "poll_seconds", "0.5"))
    if _checked(form, "skip_mux"):
        args.append("--skip-mux")
    return args


def _build_video_audio_sync(form: dict[str, str]) -> list[str]:
    video = _value(form, "video")
    audio = _value(form, "audio")
    output = _value(form, "output")
    if not video or not audio or not output:
        raise ValueError("Video/audio sync requires video, audio, and output paths.")

    args = ["--video", video, "--audio", audio, "--output", output]
    _add_opt(args, "--sample-rate", _value(form, "sample_rate", "16000"))
    _add_opt(args, "--analysis-seconds", _value(form, "analysis_seconds"))
    _add_opt(args, "--max-offset-seconds", _value(form, "max_offset_seconds", "120"))
    _add_opt(args, "--min-confidence", _value(form, "min_confidence", "0.2"))
    if _checked(form, "dry_run"):
        args.append("--dry-run")
    if _checked(form, "force"):
        args.append("--force")
    return args


TOOL_BUILDERS: dict[str, tuple[str, Callable[[dict[str, str]], list[str]]]] = {
    "transcribe": ("music_prod.transcribe_pipeline", _build_transcribe),
    "cakewalk_setup": ("music_prod.cakewalk_setup", _build_cakewalk_setup),
    "cakewalk_video": ("music_prod.cakewalk_video", _build_cakewalk_video),
    "sync_capture": ("music_prod.sync_capture", _build_sync_capture),
    "video_audio_sync": ("music_prod.video_audio_sync", _build_video_audio_sync),
}


async def _read_form_data(request: Request) -> dict[str, str]:
    # Prefer Starlette form parsing when available. If python-multipart is missing,
    # fall back to parsing classic browser x-www-form-urlencoded payloads.
    try:
        raw_form = await request.form()
        return {k: str(v) for k, v in raw_form.items()}
    except AssertionError:
        body = (await request.body()).decode("utf-8", errors="replace")
        parsed = parse_qs(body, keep_blank_values=True)
        return {k: v[-1] if v else "" for k, v in parsed.items()}


async def _append_output(job_id: str, line: str) -> None:
    async with JOBS_LOCK:
        job = JOBS[job_id]
        job.output.append(line)
        if len(job.output) > MAX_LOG_LINES:
            job.output = job.output[-MAX_LOG_LINES:]


async def _run_job(job_id: str) -> None:
    async with JOBS_LOCK:
        job = JOBS[job_id]
        job.status = "running"
        job.started_at = datetime.now().isoformat(timespec="seconds")
        command = job.command[:]

    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(Path.cwd()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
        )
        async with JOBS_LOCK:
            RUNNING_PROCS[job_id] = proc

        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            await _append_output(job_id, raw.decode(errors="replace").rstrip())

        return_code = await proc.wait()
        async with JOBS_LOCK:
            job = JOBS[job_id]
            job.return_code = return_code
            job.status = "succeeded" if return_code == 0 else "failed"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
    except Exception as exc:
        await _append_output(job_id, f"runner error: {exc}")
        async with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "failed"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
    finally:
        async with JOBS_LOCK:
            RUNNING_PROCS.pop(job_id, None)


def _render_jobs_html(jobs: list[Job]) -> str:
    chunks: list[str] = []
    for job in jobs:
        logs = "\n".join(html.escape(line) for line in job.output)
        command = html.escape(" ".join(shlex.quote(part) for part in job.command))
        controls = ""
        if job.status == "running":
            controls = """
              <div class="job-controls">
                <button type="button" onclick="jobAction('{id}','q')">Send q</button>
                <button type="button" class="danger" onclick="jobAction('{id}','stop')">Stop</button>
              </div>
            """.format(id=html.escape(job.id))
        chunks.append(
            """
            <section class="job">
              <div><strong>{id}</strong> | <code>{tool}</code> | <span class="status {status}">{status}</span></div>
              <div>created: {created} | started: {started} | finished: {finished} | rc: {rc}</div>
              {controls}
              <div><code>{command}</code></div>
              <pre>{logs}</pre>
            </section>
            """.format(
                id=html.escape(job.id),
                tool=html.escape(job.tool),
                status=html.escape(job.status),
                created=html.escape(job.created_at),
                started=html.escape(job.started_at or "-"),
                finished=html.escape(job.finished_at or "-"),
                rc=html.escape(str(job.return_code) if job.return_code is not None else "-"),
                controls=controls,
                command=command,
                logs=logs,
            )
        )
    return "\n".join(chunks) if chunks else "<p>No jobs yet.</p>"


def _render_page(error: str | None, jobs: list[Job]) -> str:
    error_block = f"<div class='error'>{html.escape(error)}</div>" if error else ""
    jobs_html = _render_jobs_html(jobs)

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>music-prod web runner</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --surface: #ffffff;
      --ink: #101828;
      --muted: #475467;
      --border: #d0d5dd;
      --accent: #0b63ce;
      --err: #b42318;
      --ok: #067647;
    }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: Segoe UI, Arial, sans-serif; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }}
    label {{ display: block; font-size: 12px; color: var(--muted); margin: 8px 0 4px; }}
    input, select {{ width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }}
    button {{ margin-top: 10px; background: var(--accent); color: #fff; border: none; padding: 8px 12px; border-radius: 6px; cursor: pointer; }}
    .check {{ display: flex; gap: 8px; align-items: center; margin-top: 8px; }}
    .check input {{ width: auto; }}
    .error {{ background: #fef3f2; color: var(--err); border: 1px solid #fecdca; border-radius: 8px; padding: 10px; margin-bottom: 12px; }}
    .job {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin: 8px 0; }}
    .job-controls {{ margin: 8px 0; display: flex; gap: 8px; }}
    .job-controls button {{ margin-top: 0; padding: 6px 10px; }}
    .job-controls .danger {{ background: #b42318; }}
    .status.running {{ color: var(--accent); }}
    .status.succeeded {{ color: var(--ok); }}
    .status.failed {{ color: var(--err); }}
    pre {{ background: #0f172a; color: #d1fae5; border-radius: 6px; padding: 8px; max-height: 220px; overflow: auto; }}
  </style>
</head>
<body>
<main>
  <h1>music-prod web runner</h1>
  <p>Runs existing CLI tools in background jobs.</p>
  {error_block}

  <div class="grid">
    <form class="card" method="post" action="/run/transcribe">
      <h2>Transcribe Pipeline</h2>
      <label>Input URL or file</label><input name="input" required />
      <label>Workdir</label><input name="workdir" value="transcribe_out" />
      <label>Model</label><input name="model" value="htdemucs" />
      <label>Device</label><input name="device" value="auto" />
      <label>Stems</label><input name="stems" value="vocals,bass,other" />
      <div class="check"><input type="checkbox" name="analyze_key" id="analyze_key" /><label for="analyze_key">Analyze key</label></div>
      <label>Key source</label><select name="key_source"><option value="midi">midi</option><option value="audio">audio</option><option value="both">both</option></select>
      <label>Key stems</label><input name="key_stems" value="bass,other" />
      <button type="submit">Run</button>
    </form>

    <form class="card" method="post" action="/run/cakewalk_setup">
      <h2>Cakewalk Setup</h2>
      <label>Mode</label><select name="input_mode"><option value="instrumental">instrumental</option><option value="stems">stems dir</option></select>
      <label>Instrumental file</label><input name="instrumental" />
      <label>Stems dir</label><input name="stems_dir" />
      <label>Projects root</label><input name="projects_root" value="projects" />
      <label>Template (.cwt/.cwp)</label><input name="template" />
      <label>Song name</label><input name="song_name" />
      <label>Lead-in seconds</label><input name="lead_in_seconds" value="10" />
      <div class="check"><input type="checkbox" name="force" id="cw_force" /><label for="cw_force">Force reuse existing folder</label></div>
      <button type="submit">Run</button>
    </form>

    <form class="card" method="post" action="/run/cakewalk_video">
      <h2>Cakewalk Video</h2>
      <label>Output file</label><input name="output_file" required />
      <label>Input device</label><input name="input_device" value="video=c922 Pro Stream Webcam" required />
      <label>Audio device</label><input name="audio_device" value="Microphone (C922 Pro Stream Webcam)" />
      <label>Input format</label><input name="input_format" value="dshow" />
      <button type="submit">Run</button>
    </form>

    <form class="card" method="post" action="/run/sync_capture">
      <h2>Sync Capture</h2>
      <label>Audio file (exclusive with cwp audio)</label><input name="audio_file" />
      <label>CWP audio dir (exclusive with audio file)</label><input name="cwp_audio" />
      <label>Input device</label><input name="input_device" required />
      <label>Input format</label><input name="input_format" value="dshow" />
      <label>Output dir</label><input name="output_dir" />
      <label>Inactivity seconds</label><input name="inactivity_seconds" value="5" />
      <label>Poll seconds</label><input name="poll_seconds" value="0.5" />
      <div class="check"><input type="checkbox" name="skip_mux" id="skip_mux" /><label for="skip_mux">Skip mux</label></div>
      <button type="submit">Run</button>
    </form>

    <form class="card" method="post" action="/run/video_audio_sync">
      <h2>Video Audio Sync</h2>
      <label>Video path</label><input name="video" required />
      <label>Audio path</label><input name="audio" required />
      <label>Output path</label><input name="output" required />
      <label>Sample rate</label><input name="sample_rate" value="16000" />
      <label>Analysis seconds</label><input name="analysis_seconds" />
      <label>Max offset seconds</label><input name="max_offset_seconds" value="120" />
      <label>Min confidence</label><input name="min_confidence" value="0.2" />
      <div class="check"><input type="checkbox" name="dry_run" id="dry_run" /><label for="dry_run">Dry run</label></div>
      <div class="check"><input type="checkbox" name="force" id="vas_force" /><label for="vas_force">Force overwrite</label></div>
      <button type="submit">Run</button>
    </form>
  </div>

  <h2 style="margin-top:16px">Jobs</h2>
  <div id="jobs-panel">
    {jobs_html}
  </div>
</main>
<script>
  const storageKey = "music-prod-web-form-state-v1";

  function restoreFormState() {{
    const raw = localStorage.getItem(storageKey);
    if (!raw) return;
    let state = {{}};
    try {{ state = JSON.parse(raw); }} catch (_) {{ return; }}

    document.querySelectorAll("form input, form select").forEach((el) => {{
      const key = el.form.action + "::" + el.name;
      if (!(key in state)) return;
      if (el.type === "checkbox") {{
        el.checked = Boolean(state[key]);
      }} else {{
        el.value = state[key];
      }}
    }});
  }}

  function saveFormState() {{
    const state = {{}};
    document.querySelectorAll("form input, form select").forEach((el) => {{
      if (!el.name) return;
      const key = el.form.action + "::" + el.name;
      state[key] = el.type === "checkbox" ? el.checked : el.value;
    }});
    localStorage.setItem(storageKey, JSON.stringify(state));
  }}

  async function refreshJobsPanel() {{
    try {{
      const res = await fetch("/jobs-panel", {{ cache: "no-store" }});
      if (!res.ok) return;
      const html = await res.text();
      const panel = document.getElementById("jobs-panel");
      if (panel) panel.innerHTML = html;
    }} catch (_) {{
      // keep silent; next poll will retry
    }}
  }}

  async function jobAction(jobId, action) {{
    try {{
      await fetch(`/jobs/${{jobId}}/action/${{action}}`, {{
        method: "POST",
        cache: "no-store",
      }});
    }} catch (_) {{
      // ignore transient failures; poller keeps UI fresh
    }}
    await refreshJobsPanel();
  }}

  document.querySelectorAll("form input, form select").forEach((el) => {{
    el.addEventListener("input", saveFormState);
    el.addEventListener("change", saveFormState);
  }});

  restoreFormState();
  setInterval(refreshJobsPanel, 3000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> str:
    error = request.query_params.get("error")
    async with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
    return _render_page(error, jobs)


@app.get("/jobs-panel", response_class=HTMLResponse)
async def jobs_panel() -> str:
    async with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
    return _render_jobs_html(jobs)


@app.post("/jobs/{job_id}/action/{action}")
async def control_job(job_id: str, action: str) -> HTMLResponse:
    async with JOBS_LOCK:
        proc = RUNNING_PROCS.get(job_id)
        job = JOBS.get(job_id)

    if proc is None or job is None:
        return HTMLResponse("not found", status_code=404)
    if proc.returncode is not None:
        return HTMLResponse("already finished", status_code=409)

    if action == "q":
        if proc.stdin is None:
            return HTMLResponse("stdin unavailable", status_code=409)
        try:
            proc.stdin.write(b"q\n")
            await proc.stdin.drain()
            await _append_output(job_id, "[web] sent q")
            return HTMLResponse("ok", status_code=200)
        except (BrokenPipeError, ConnectionError, OSError):
            return HTMLResponse("stdin closed", status_code=409)

    if action == "stop":
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                await _append_output(job_id, "[web] ctrl+break requested")
                return HTMLResponse("ok", status_code=200)
            proc.terminate()
            await _append_output(job_id, "[web] terminate requested")
            return HTMLResponse("ok", status_code=200)
        except ProcessLookupError:
            return HTMLResponse("already finished", status_code=409)
        except OSError:
            try:
                proc.kill()
                await _append_output(job_id, "[web] kill requested")
                return HTMLResponse("ok", status_code=200)
            except (ProcessLookupError, OSError):
                return HTMLResponse("cannot stop process", status_code=500)

    return HTMLResponse("unknown action", status_code=400)


@app.post("/run/{tool_name}")
async def run_tool(tool_name: str, request: Request) -> RedirectResponse:
    if tool_name not in TOOL_BUILDERS:
        return RedirectResponse(url=f"/?error={quote_plus('Unknown tool')}", status_code=303)

    form = await _read_form_data(request)
    module, builder = TOOL_BUILDERS[tool_name]

    try:
        cli_args = builder(form)
    except ValueError as exc:
        return RedirectResponse(url=f"/?error={quote_plus(str(exc))}", status_code=303)

    command = [sys.executable, "-m", module, *cli_args]
    job_id = uuid.uuid4().hex[:10]
    job = Job(id=job_id, tool=tool_name, command=command)

    async with JOBS_LOCK:
        JOBS[job_id] = job

    asyncio.create_task(_run_job(job_id))
    return RedirectResponse(url="/", status_code=303)


def main() -> None:
    import uvicorn

    uvicorn.run("music_prod.web_app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
