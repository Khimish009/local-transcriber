"""T12.1 — installing the shared-trunk embedding step (numerics: benchmark.equivalence)."""

import logging
import sys
import types

import pytest

from worker.pipeline import models
from worker.pipeline.embeddings import install_shared_trunk_embeddings, shared_trunk_embeddings

CHANNELS_LAST = object()


class FakeResNet:
    def __init__(self) -> None:
        self.memory_format = None

    def to(self, memory_format=None):
        self.memory_format = memory_format
        return self


class StockPipeline:
    """Stands in for pyannote's SpeakerDiarization: only what the installer touches."""

    def __init__(self, with_resnet: bool = True) -> None:
        self.training = False
        self.resnet = FakeResNet()
        model = types.SimpleNamespace(resnet=self.resnet) if with_resnet else object()
        self._embedding = types.SimpleNamespace(model_=model)

    def get_embeddings(self, file, binary_segmentations, exclude_overlap=False, hook=None):
        return ("stock", file, exclude_overlap)


@pytest.fixture(autouse=True)
def fake_torch(monkeypatch):
    torch = types.ModuleType("torch")
    torch.channels_last = CHANNELS_LAST
    monkeypatch.setitem(sys.modules, "torch", torch)


def test_install_replaces_the_embedding_step() -> None:
    pipeline = StockPipeline()

    assert install_shared_trunk_embeddings(pipeline) is True

    assert pipeline.get_embeddings.__func__ is shared_trunk_embeddings
    assert pipeline.resnet.memory_format is CHANNELS_LAST


def test_other_embedding_models_keep_the_stock_step(caplog) -> None:
    pipeline = StockPipeline(with_resnet=False)

    with caplog.at_level(logging.WARNING):
        assert install_shared_trunk_embeddings(pipeline) is False

    assert pipeline.get_embeddings("f", None) == ("stock", "f", False)
    assert "keeping pyannote's embedding step" in caplog.text


def test_training_mode_falls_back_to_the_stock_step() -> None:
    """Hyper-parameter tuning relies on pyannote's own embedding cache."""
    pipeline = StockPipeline()
    install_shared_trunk_embeddings(pipeline)
    pipeline.training = True

    assert pipeline.get_embeddings("f", None, exclude_overlap=True) == ("stock", "f", True)


def test_the_worker_singleton_gets_the_shared_trunk(settings, monkeypatch) -> None:
    models.reset_pipeline_cache()
    stock = StockPipeline()
    installed: list[object] = []
    monkeypatch.setattr(models, "load_diarization_pipeline", lambda *args: stock)
    monkeypatch.setattr(models, "install_shared_trunk_embeddings", installed.append)

    try:
        assert models.get_diarization_pipeline(settings, None) is stock
        models.get_diarization_pipeline(settings, None)
    finally:
        models.reset_pipeline_cache()

    assert installed == [stock], "installed once, on the cached instance"
