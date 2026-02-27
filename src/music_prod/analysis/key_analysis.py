from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class KeyResult:
    source: str                 # "midi" or "audio"
    key: str                    # e.g. "E minor"
    tonic: str                  # e.g. "E"
    mode: str                   # "major" | "minor" | "dorian" | etc (best effort)
    scale_degrees: list[str]    # e.g. ["E", "F#", "G", "A", "B", "C", "D"]
    confidence: float | None = None  # audio confidence if available

def _midi_key(midi_path: Path) -> KeyResult:
    from music21 import converter

    score = converter.parse(str(midi_path))
    k = score.analyze("key")  # music21.key.Key

    # Build a simple scale degree list (diatonic scale)
    # music21 doesn't always label modes beyond major/minor cleanly from key analysis,
    # so treat mode as major/minor unless you add a more advanced modal classifier later.
    tonic = str(k.tonic)
    mode = str(k.mode)  # usually "major" or "minor"

    # Create pitch names for the scale degrees
    # (music21 provides k.getScale(); pitch names may include enharmonics)
    scale = k.getScale()
    degrees = [p.name for p in scale.getPitches(tonic + "3", tonic + "4")[:7]]

    return KeyResult(
        source="midi",
        key=str(k),
        tonic=tonic,
        mode=mode,
        scale_degrees=degrees,
        confidence=None,
    )


def _audio_key(audio_path: Path) -> KeyResult:
    """
    Audio key estimate using chroma + a simple Krumhansl-Schmuckler style correlation.
    This is a reasonable baseline and works well for tonal material.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio_path), mono=True)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)

    # Krumhansl major/minor key profiles
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    def rotate(v, n):
        return np.roll(v, n)

    keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    # Correlate against each rotation (candidate tonic)
    best = None  # (score, tonic, mode)
    for i, tonic in enumerate(keys):
        maj_score = np.corrcoef(profile, rotate(major, i))[0, 1]
        min_score = np.corrcoef(profile, rotate(minor, i))[0, 1]
        for mode, score in [("major", float(maj_score)), ("minor", float(min_score))]:
            if best is None or score > best[0]:
                best = (score, tonic, mode)

    assert best is not None
    score, tonic, mode = best

    # Scale degrees (simple diatonic degrees for major/minor)
    if mode == "major":
        steps = [0, 2, 4, 5, 7, 9, 11]
    else:
        steps = [0, 2, 3, 5, 7, 8, 10]

    tonic_i = keys.index(tonic)
    degrees = [keys[(tonic_i + s) % 12] for s in steps]

    return KeyResult(
        source="audio",
        key=f"{tonic} {mode}",
        tonic=tonic,
        mode=mode,
        scale_degrees=degrees,
        confidence=max(0.0, min(1.0, (score + 1.0) / 2.0)),  # map [-1,1] -> [0,1]
    )


def analyze_key_and_scale(
    *,
    out_dir: Path,
    midi_files: list[Path] | None = None,
    audio_files: list[Path] | None = None,
) -> dict:
    """
    Analyze key/scale from MIDI (preferred) and/or audio, write JSON + Markdown report.
    Returns the parsed dict.
    """
    out_dir = Path(out_dir)
    analysis_dir = out_dir / "30_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    results: list[KeyResult] = []

    if midi_files:
        for m in midi_files:
            if m and m.exists():
                results.append(_midi_key(m))

    if audio_files:
        for a in audio_files:
            if a and a.exists():
                results.append(_audio_key(a))

    payload = {
        "results": [asdict(r) for r in results],
    }

    (analysis_dir / "key_analysis.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Basic Markdown summary
    lines = ["# Key / Scale Analysis", ""]
    for r in results:
        conf = f" (confidence {r.confidence:.2f})" if r.confidence is not None else ""
        lines += [
            f"## Source: {r.source}",
            f"- **Key:** {r.key}{conf}",
            f"- **Tonic:** {r.tonic}",
            f"- **Mode:** {r.mode}",
            f"- **Scale degrees:** {', '.join(r.scale_degrees)}",
            "",
        ]

    (analysis_dir / "key_analysis.md").write_text("\n".join(lines), encoding="utf-8")

    return payload
