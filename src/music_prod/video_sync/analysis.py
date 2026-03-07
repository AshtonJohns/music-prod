from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, resample_poly, stft


@dataclass(frozen=True)
class OffsetResult:
    offset_seconds: float
    confidence: float
    method: str
    peak_lag_samples: int


def _to_float32(samples: np.ndarray) -> np.ndarray:
    if samples.dtype.kind in {"i", "u"}:
        scale = max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
        return (samples.astype(np.float32) / float(scale)).astype(np.float32, copy=False)
    return samples.astype(np.float32, copy=False)


def load_wav_mono(path: Path) -> tuple[int, np.ndarray]:
    sample_rate, data = wavfile.read(path)
    audio = _to_float32(np.asarray(data))
    if audio.ndim == 2:
        audio = np.mean(audio, axis=1)
    return int(sample_rate), audio.astype(np.float32, copy=False)


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    centered = audio - float(np.mean(audio))
    scale = float(np.std(centered))
    if scale > 1e-8:
        centered = centered / scale
    return centered.astype(np.float32, copy=False)


def _smooth_envelope(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    frame = max(64, int(sample_rate * 0.02))
    kernel = np.ones(frame, dtype=np.float32) / float(frame)
    env = np.convolve(np.abs(audio), kernel, mode="same")
    return normalize_audio(env)


def _spectral_flux(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    _, _, zxx = stft(audio, fs=sample_rate, nperseg=512, noverlap=384, boundary=None)
    mag = np.abs(zxx)
    delta = np.diff(mag, axis=1, prepend=mag[:, :1])
    flux = np.maximum(delta, 0.0).sum(axis=0).astype(np.float32, copy=False)
    if flux.size == 0:
        raise ValueError("Could not compute spectral flux.")
    return normalize_audio(flux)


def _apply_analysis_window(
    video_audio: np.ndarray,
    wav_audio: np.ndarray,
    sample_rate: int,
    analysis_seconds: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    if not analysis_seconds or analysis_seconds <= 0:
        return video_audio, wav_audio
    limit = int(sample_rate * analysis_seconds)
    return video_audio[:limit], wav_audio[:limit]


def _max_offset_samples(max_offset_seconds: float | None, sample_rate: float) -> int | None:
    if not max_offset_seconds or max_offset_seconds <= 0:
        return None
    return int(round(max_offset_seconds * sample_rate))


def _correlation_offset(
    ref_signal: np.ndarray,
    target_signal: np.ndarray,
    *,
    max_lag: int | None,
) -> tuple[int, float]:
    corr = correlate(ref_signal, target_signal, mode="full", method="fft")
    lags = np.arange(-target_signal.size + 1, ref_signal.size, dtype=np.int64)
    if max_lag is not None:
        mask = np.abs(lags) <= max_lag
        corr = corr[mask]
        lags = lags[mask]
    if corr.size == 0:
        raise ValueError("No correlation candidates in selected lag window.")
    abs_corr = np.abs(corr)
    idx = int(np.argmax(abs_corr))
    peak = float(abs_corr[idx])
    mean = float(np.mean(abs_corr))
    std = float(np.std(abs_corr))
    confidence = peak / max(mean + (2.0 * std), 1e-8)
    confidence = min(max(confidence, 0.0), 1.0)
    return int(lags[idx]), confidence


def detect_offset(
    *,
    video_wav: Path,
    clean_wav: Path,
    analysis_sample_rate: int,
    analysis_seconds: float | None,
    max_offset_seconds: float | None,
) -> OffsetResult:
    sr_video, video_audio = load_wav_mono(video_wav)
    sr_clean, clean_audio = load_wav_mono(clean_wav)
    if video_audio.size == 0 or clean_audio.size == 0:
        raise ValueError("One of the analysis WAV files has no samples.")

    if sr_video != analysis_sample_rate:
        video_audio = resample_poly(video_audio, analysis_sample_rate, sr_video)
    if sr_clean != analysis_sample_rate:
        clean_audio = resample_poly(clean_audio, analysis_sample_rate, sr_clean)

    video_audio, clean_audio = _apply_analysis_window(
        normalize_audio(video_audio),
        normalize_audio(clean_audio),
        analysis_sample_rate,
        analysis_seconds,
    )
    if video_audio.size < 128 or clean_audio.size < 128:
        raise ValueError("Audio too short for offset analysis.")

    max_lag = _max_offset_samples(max_offset_seconds, analysis_sample_rate)
    env_video = _smooth_envelope(video_audio, analysis_sample_rate)
    env_clean = _smooth_envelope(clean_audio, analysis_sample_rate)
    lag_env, conf_env = _correlation_offset(env_video, env_clean, max_lag=max_lag)
    if conf_env >= 0.25:
        return OffsetResult(
            offset_seconds=float(lag_env) / float(analysis_sample_rate),
            confidence=conf_env,
            method="normalized_cross_correlation",
            peak_lag_samples=lag_env,
        )

    flux_video = _spectral_flux(video_audio, analysis_sample_rate)
    flux_clean = _spectral_flux(clean_audio, analysis_sample_rate)
    flux_rate = analysis_sample_rate / 128.0
    max_flux_lag = _max_offset_samples(max_offset_seconds, flux_rate)
    lag_flux, conf_flux = _correlation_offset(flux_video, flux_clean, max_lag=max_flux_lag)
    return OffsetResult(
        offset_seconds=float(lag_flux) / flux_rate,
        confidence=conf_flux,
        method="spectral_flux_correlation",
        peak_lag_samples=lag_flux,
    )
