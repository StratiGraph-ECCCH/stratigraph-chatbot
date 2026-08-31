"""Speech-to-text — an INTERFACE, because the engine is not ours.

The design note is explicit: this service does not implement speech recognition.
Whisper / whisper.cpp / Kyutai run **downstream on the Field Computing Node**,
offline-served and adapted to archaeological vocabularies, and ARC's ATRIUM
already captures voice and hands over a transcript.

So what lives here is the seam and two implementations of it:

* **passthrough** — the transcript was produced elsewhere. This is not a test
  stub: it is the ATRIUM case, which is a first-class deployment, and it is why
  the whole assistant runs with no audio stack installed at all;
* **Whisper** — the node's own engine, **config-gated exactly like the MinIO
  store**: it exists when it is configured, it is never chosen silently, and a
  half-configuration refuses with a sentence instead of pretending.

Nothing heavy is imported at module load. A field node that only ever receives
text from ATRIUM must not need a model on disk, and the headless tests must run
on a laptop with no GPU.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Protocol


class SpeechToText(Protocol):
    """Audio in, text out. That is the entire contract."""

    def transcribe(self, audio: bytes, *, language: str = "") -> str:
        ...


class PassthroughSTT:
    """The transcript already exists — ATRIUM produced it, or a person typed it.

    `transcribe` decodes the bytes as UTF-8 text, which is what a caller sending
    "the transcription is provided" actually sends. It is a deployment, not a
    fake: on a node where ARC's app does the listening, this is the correct
    implementation and the honest one.
    """

    name = "passthrough"
    available = True

    def transcribe(self, audio: bytes, *, language: str = "") -> str:
        if isinstance(audio, str):
            return audio.strip()
        try:
            return (audio or b"").decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError(
                "the passthrough engine expects a TRANSCRIPT, and these bytes "
                "are not text. Configure a speech engine on the node "
                "(EM_CHATBOT_WHISPER_MODEL) or send the transcription."
            ) from exc


class WhisperSTT:
    """The node's engine. Config-gated, and it fails at CONSTRUCTION.

    Same rule as the object store: a service that comes up claiming a capability
    it does not have is worse than one that refuses to come up. The model path
    is configuration; a missing library or a missing model is a sentence naming
    which, not a stack trace at the first recording.
    """

    name = "whisper"
    available = True

    def __init__(self, model_path: str, *, compute_type: str = "int8") -> None:
        if not model_path:
            raise ValueError("WhisperSTT needs a model path")
        self.model_path = model_path
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise NotImplementedError(
                "speech-to-text on this node needs the `faster-whisper` extra "
                "(pip install stratigraph-chatbot[stt]); until then send the "
                "transcript and the passthrough engine handles it") from exc
        if not os.path.exists(model_path):
            raise NotImplementedError(
                f"no speech model at {model_path!r}. Download one onto the "
                f"field node, or unset EM_CHATBOT_WHISPER_MODEL to use the "
                f"transcript the client sends.")
        self._model = WhisperModel(model_path, compute_type=compute_type)

    def transcribe(self, audio: bytes, *, language: str = "") -> str:
        import io

        # `None` and not `""`: faster-whisper reads None as "detect it", and an
        # empty string is not the same request. The default used to be a
        # hard-coded "it", which is how a library signature came to assume a
        # language — the same drift we took out of the page.
        segments, _info = self._model.transcribe(io.BytesIO(audio),
                                                 language=language or None)
        return " ".join(segment.text.strip() for segment in segments).strip()


def stt_from_env(environ: Optional[Dict[str, str]] = None) -> SpeechToText:
    """Whisper when the node names a model; passthrough otherwise.

    Never a silent third thing, and never a fallback FROM Whisper: if a model is
    configured and cannot be loaded, the process refuses. An operator who put a
    model on a node believes recordings are being transcribed on it.
    """
    env = dict(environ if environ is not None else os.environ)
    model_path = (env.get("EM_CHATBOT_WHISPER_MODEL") or "").strip()
    if model_path:
        return WhisperSTT(model_path)
    return PassthroughSTT()


def describe(engine: Any) -> str:
    """For `/health`: which engine is actually listening."""
    if isinstance(engine, WhisperSTT):
        return f"whisper ({engine.model_path})"
    return "passthrough (the client sends the transcript — e.g. ATRIUM)"
