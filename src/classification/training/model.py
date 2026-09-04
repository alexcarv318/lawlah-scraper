import pickle
from pathlib import Path
from typing import IO

import torch
from torch import Tensor, nn
from transformers import AutoModel

from src.classification.training.labels import LabelScheme


class CheckpointUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> object:
        if name == "LabelScheme":
            return LabelScheme

        found: object = super().find_class(module, name)
        return found


class checkpoint_pickle:
    Unpickler = CheckpointUnpickler

    @staticmethod
    def load(file: IO[bytes]) -> object:
        return CheckpointUnpickler(file).load()

    dump = staticmethod(pickle.dump)
    dumps = staticmethod(pickle.dumps)
    loads = staticmethod(pickle.loads)


def read_checkpoint(path: Path, device: torch.device) -> dict[str, object]:
    loaded = torch.load(path, map_location=device, weights_only=False, pickle_module=checkpoint_pickle)
    if not isinstance(loaded, dict):
        raise TypeError("Checkpoint must be a dict")

    return loaded


def checkpoint_weights(payload: dict[str, object]) -> dict[str, Tensor]:
    weights = payload.get("model")
    if not isinstance(weights, dict):
        raise TypeError("Checkpoint is missing model weights")

    tensors: dict[str, Tensor] = {}
    for key, value in weights.items():
        if not isinstance(key, str) or not isinstance(value, Tensor):
            raise TypeError("Checkpoint model weights must be a tensor state dict")

        tensors[key] = value

    return tensors


class TextClassifier(nn.Module):
    def __init__(self, encoder_name: str, label_count: int) -> None:
        super().__init__()

        self.encoder = AutoModel.from_pretrained(encoder_name, attn_implementation="eager")
        hidden_size = int(self.encoder.config.hidden_size)
        self.head = nn.Linear(hidden_size, label_count)

    def forward(self, input_ids: Tensor, attention_mask: Tensor) -> Tensor:
        encoded = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled = encoded.last_hidden_state[:, 0]
        if pooled.dtype != torch.float32:
            pooled = pooled.float()

        logits = self.head(pooled)
        if not isinstance(logits, Tensor):
            raise TypeError("Classification head must return a tensor")

        return logits
