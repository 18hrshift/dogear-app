"""Stand-ins for a Piper voice's files and for onnxruntime, so no test loads a real model."""

import json
from pathlib import Path

import numpy as np
from piper.phoneme_ids import DEFAULT_PHONEME_ID_MAP


def write_voice_config(path: Path, **changes) -> dict:
    """A voice config in the shape Piper's have, with Piper's usual phoneme ids."""
    voice = {
        "audio": {"sample_rate": 22050},
        "espeak": {"voice": "en-us"},
        "phoneme_type": "espeak",
        "num_symbols": 256,
        "num_speakers": 1,
        "inference": {"noise_scale": 0.667, "length_scale": 1, "noise_w": 0.8},
        "phoneme_id_map": DEFAULT_PHONEME_ID_MAP,
        **changes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(voice), encoding="utf-8")
    return voice


class FakeSession:
    """Stands in for onnxruntime's session. Returns 256 samples per phoneme id, at 2.0,
    louder than audio may be, so clipping shows."""

    instances: list["FakeSession"] = []

    def __init__(self, model: str, options, providers: list[str]) -> None:
        self.model = model
        self.options = options
        self.providers = providers
        self.feeds: list[dict[str, np.ndarray]] = []
        FakeSession.instances.append(self)

    def run(self, outputs, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.feeds.append(feeds)
        count = feeds["input"].shape[1] * 256
        return [np.full((1, 1, count), 2.0, dtype=np.float32)]
