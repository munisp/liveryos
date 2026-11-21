#!/usr/bin/env python3
"""LongCat-AudioDiT serving wrapper (voice concierge).

Thin HTTP front over the upstream inference API
(https://github.com/meituan-longcat/LongCat-AudioDiT, MIT). The Pipecat
runner and the voice-drop worker POST /synthesize; /health gates the
honest engine selection in livery_core.adapters.voice.longcat.

HARD REQUIREMENTS — this service does not fake anything:
- CUDA GPU (1B: ~8GB VRAM fp16 VAE; 3.5B: ~16GB+). No GPU → the service
  REFUSES TO START (exit 3) rather than serving 10x-slow CPU audio that
  stalls phone calls. The platform falls back to piper, labeled.
- Model weights from HuggingFace (meituan-longcat/LongCat-AudioDiT-1B
  or -3.5B). Missing weights → exit 4 with the exact download command.

Run:  LONGCAT_MODEL_KEY=longcat-1b python3 services/longcat_tts_server.py
Env:  LONGCAT_MODEL_KEY, LONGCAT_PORT (default 8100), VOICE_PROFILES_DIR
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MODELS = {
    "longcat-1b": "meituan-longcat/LongCat-AudioDiT-1B",
    "longcat-3.5b": "meituan-longcat/LongCat-AudioDiT-3.5B",
}
SAMPLE_RATE = 24000

_model = None
_tokenizer = None
_config = {}


def _load_model() -> None:
    """Load AudioDiT per upstream README. Exits honestly on no-GPU/no-weights."""
    global _model, _tokenizer
    try:
        import torch
    except ImportError:
        sys.exit("torch not installed — see services/longcat-requirements.txt")
    if not torch.cuda.is_available():
        _die(3, "CUDA GPU not available. LongCat-AudioDiT on CPU is too slow "
                "for telephony; LiveryOS policy is piper fallback (labeled), "
                "not stalled calls. Start without this service instead.")
    model_key = os.environ.get("LONGCAT_MODEL_KEY", "longcat-1b")
    model_dir = MODELS.get(model_key)
    if model_dir is None:
        _die(5, f"LONGCAT_MODEL_KEY must be one of {sorted(MODELS)}")
    try:
        import audiodit  # upstream package, auto-registers with transformers
        from audiodit import AudioDiTModel
        from transformers import AutoTokenizer
    except ImportError:
        _die(4, "audiodit package missing. Clone "
                "https://github.com/meituan-longcat/LongCat-AudioDiT and "
                "pip install -r requirements.txt (see "
                "services/longcat-requirements.txt)")
    try:
        _model = AudioDiTModel.from_pretrained(model_dir).to("cuda")
    except Exception as e:
        _die(4, f"model weights unavailable ({e}). Download:\n"
                f"  huggingface-cli download {model_dir}")
    _model.vae.to_half()  # VAE fp16, per upstream
    _model.eval()
    _tokenizer = AutoTokenizer.from_pretrained(_model.config.text_encoder_model)
    _config.update(model_key=model_key, model_dir=model_dir)


def _die(code: int, msg: str):
    print(f"longcat-tts: {msg}", file=sys.stderr)
    sys.exit(code)


def _synthesize(payload: dict) -> dict:
    """One utterance. Returns audio path + measured duration."""
    import soundfile as sf
    import torch

    text = payload["text"]
    inputs = _tokenizer([text], padding="longest", return_tensors="pt")
    t0 = time.monotonic()
    with torch.inference_mode():
        output = _model(
            input_ids=inputs.input_ids.to("cuda"),
            attention_mask=inputs.attention_mask.to("cuda"),
            duration=int(payload.get("duration", 96)),
            steps=int(payload.get("steps", 16)),
            cfg_strength=float(payload.get("cfg_strength", 4.0)),
            guidance_method=payload.get("guidance_method", "apg"),
        )
    wav = output.waveform.squeeze().cpu().numpy()
    out_dir = Path(os.environ.get("LONGCAT_OUT_DIR", "/tmp/longcat-out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{int(t0 * 1000)}-{os.getpid()}.wav"
    sf.write(str(out_path), wav, SAMPLE_RATE)
    return {
        "audio_ref": str(out_path),
        "sample_rate": SAMPLE_RATE,
        "duration_ms": int(len(wav) / SAMPLE_RATE * 1000),
        "engine": _config["model_key"],
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            # Health means: model loaded on GPU. Anything less is NOT healthy.
            self._json(200 if _model is not None else 503,
                       {"ok": _model is not None, **_config})
        else:
            self._json(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path != "/synthesize":
            self._json(404, {"error": "unknown path"})
            return
        try:
            payload = json.loads(self.rfile.read(
                int(self.headers["Content-Length"])))
            if not payload.get("text", "").strip():
                self._json(400, {"error": "text required — silence is not "
                                          "a concierge reply"})
                return
            self._json(200, _synthesize(payload))
        except Exception as e:  # synthesis failure surfaces, never faked
            self._json(500, {"error": f"synthesis failed: {e}"})

    def log_message(self, *args):
        pass


def main() -> None:
    _load_model()
    port = int(os.environ.get("LONGCAT_PORT", "8100"))
    print(f"longcat-tts serving {_config['model_key']} on :{port} (CUDA)")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
