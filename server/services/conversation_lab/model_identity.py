"""Model metadata only; never import application settings or character identity."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ModelIdentity:
    model: str

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("select an already-running local model explicitly")

    @property
    def series(self):
        short_name = self.model.rstrip("/").rsplit("/", 1)[-1]
        # Syntactic name prefix, not a vendor-specific model-family registry.
        return re.split(r"[-_\d]", short_name, maxsplit=1)[0] or short_name

    @classmethod
    def from_config(cls, config):
        return cls(config.AI_CHAT_MODEL)

    @property
    def names(self):
        return list(dict.fromkeys([self.model, self.series]))


def resolve_model_identity(model=None, config_identity=None):
    """Always derive the series from the model selected for this run."""
    selected = model if model is not None else getattr(config_identity, "model", None)
    return ModelIdentity(selected)
