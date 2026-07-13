"""Audio-conversie voor integraties (A5 — Telegram voice-notes).

WAV (16-bit PCM) -> OGG/Opus, in-process via PyAV: de ffmpeg-libs zitten in
de av-wheel, dus er is bewust géén ffmpeg-binary in het Docker-image nodig.
Telegram's sendVoice accepteert alleen OGG/Opus als echte voice-note."""
from __future__ import annotations

import io


def wav_to_ogg_opus(wav_bytes: bytes, bitrate: int = 32_000) -> bytes:
    """Zet een WAV-bestand om naar OGG/Opus-bytes.

    Raise't bij kapotte input of een ontbrekende opus-encoder — de aanroeper
    (TelegramBridge.send_voice) vangt dat en valt terug op een tekstbericht."""
    import av

    src = av.open(io.BytesIO(wav_bytes), format="wav")
    buf = io.BytesIO()
    out = av.open(buf, mode="w", format="ogg")
    try:
        stream = out.add_stream("libopus", rate=48000)
        stream.bit_rate = bitrate
        # Opus eist 48 kHz; Piper/XTTS leveren 22.05/24 kHz -> expliciet
        # resamplen (mono is genoeg voor spraak)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
        for frame in src.decode(audio=0):
            for rf in resampler.resample(frame):
                for packet in stream.encode(rf):
                    out.mux(packet)
        for rf in resampler.resample(None):     # resampler leegtrekken
            for packet in stream.encode(rf):
                out.mux(packet)
        for packet in stream.encode(None):      # encoder leegtrekken
            out.mux(packet)
    finally:
        out.close()
        src.close()
    return buf.getvalue()
