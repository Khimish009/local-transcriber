import logging
import sys
import types

import pytest

from app.core.errors import AppError, ErrorCode
from app.services.model_cache import (
    DIARIZATION_MODEL,
    asr_checkpoint_path,
    is_asr_model_cached,
    is_model_cached,
    repo_cache_dir,
)
from worker.pipeline import models

TOKEN = "hf_secrettokenvalue"


@pytest.fixture(autouse=True)
def clean_pipeline_cache():
    models.reset_pipeline_cache()
    yield
    models.reset_pipeline_cache()


@pytest.fixture(autouse=True)
def isolate_hf_env(monkeypatch):
    for key in ("HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.delenv(key, raising=False)


def cache_the_model(settings) -> None:
    snapshot = repo_cache_dir(settings, DIARIZATION_MODEL) / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "config.yaml").write_text("fake")


class FakePipeline:
    """Stands in for `pyannote.audio.Pipeline`."""

    last_kwargs: dict = {}
    result: object | None = None
    raises: Exception | None = None

    def __init__(self) -> None:
        self.device = None

    @classmethod
    def from_pretrained(cls, model_id, **kwargs):
        cls.last_kwargs = {"model_id": model_id, **kwargs}
        if cls.raises is not None:
            raise cls.raises
        return cls.result

    def to(self, device):
        self.device = device
        return self


@pytest.fixture
def fake_ml(monkeypatch):
    """Install stand-ins for pyannote.audio and torch, which are worker-image only."""
    FakePipeline.raises = None
    FakePipeline.result = FakePipeline()
    FakePipeline.last_kwargs = {}

    pyannote = types.ModuleType("pyannote")
    pyannote_audio = types.ModuleType("pyannote.audio")
    pyannote_audio.Pipeline = FakePipeline
    pyannote.audio = pyannote_audio

    torch = types.ModuleType("torch")
    torch.device = lambda name: f"device:{name}"
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)

    monkeypatch.setitem(sys.modules, "pyannote", pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", pyannote_audio)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return FakePipeline


def test_cache_detection(settings) -> None:
    assert is_model_cached(settings) is False

    cache_the_model(settings)

    assert is_model_cached(settings) is True


def test_missing_token_fails_before_any_download(settings) -> None:
    with pytest.raises(AppError) as exc:
        models.load_diarization_pipeline(settings, hf_token=None)

    assert exc.value.code is ErrorCode.HF_TOKEN_REQUIRED


def test_cached_model_does_not_need_a_token(settings, fake_ml) -> None:
    cache_the_model(settings)

    pipeline = models.load_diarization_pipeline(settings, hf_token=None)

    assert pipeline is not None
    assert fake_ml.last_kwargs["model_id"] == DIARIZATION_MODEL
    assert fake_ml.last_kwargs["token"] is None


def test_cached_model_forces_offline_mode(settings, fake_ml, monkeypatch) -> None:
    import os

    cache_the_model(settings)

    models.load_diarization_pipeline(settings, hf_token=None)

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_CACHE"] == str(models.hf_cache_dir(settings))


def test_first_download_uses_the_token_and_stays_online(settings, fake_ml) -> None:
    import os

    models.load_diarization_pipeline(settings, hf_token=TOKEN)

    assert fake_ml.last_kwargs["token"] == TOKEN
    assert "HF_HUB_OFFLINE" not in os.environ


def test_pipeline_is_moved_to_the_configured_device(settings, fake_ml) -> None:
    pipeline = models.load_diarization_pipeline(settings, hf_token=TOKEN)

    assert pipeline.device == "device:cpu"


def test_cuda_without_gpu_fails_loudly(settings, fake_ml) -> None:
    cuda_settings = settings.model_copy(update={"device": "cuda"})

    with pytest.raises(AppError) as exc:
        models.load_diarization_pipeline(cuda_settings, hf_token=TOKEN)

    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE
    assert "cuda" in exc.value.message.lower()


def test_gated_model_returning_none_is_reported(settings, fake_ml) -> None:
    fake_ml.result = None

    with pytest.raises(AppError) as exc:
        models.load_diarization_pipeline(settings, hf_token=TOKEN)

    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE


def test_load_failure_never_leaks_the_token(settings, fake_ml, caplog) -> None:
    fake_ml.raises = RuntimeError(f"401 unauthorized for token {TOKEN}")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AppError) as exc:
            models.load_diarization_pipeline(settings, hf_token=TOKEN)

    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE
    assert TOKEN not in caplog.text
    assert TOKEN not in exc.value.message


def test_pipeline_is_loaded_once_per_process(settings, fake_ml) -> None:
    calls: list[int] = []
    original = models.load_diarization_pipeline

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    models.load_diarization_pipeline = counting
    try:
        first = models.get_diarization_pipeline(settings, TOKEN)
        second = models.get_diarization_pipeline(settings, TOKEN)
    finally:
        models.load_diarization_pipeline = original

    assert first is second
    assert len(calls) == 1


def test_token_is_not_shown_in_settings_repr(settings) -> None:
    with_token = settings.model_copy(update={"hf_token": TOKEN})

    assert TOKEN not in repr(with_token)


# --- GigaAM (T4.1) -----------------------------------------------------------------


class FakeAsrModel:
    calls: list[dict] = []
    raises: Exception | None = None

    @classmethod
    def load_model(cls, model_name, **kwargs):
        cls.calls.append({"model_name": model_name, **kwargs})
        if cls.raises is not None:
            raise cls.raises
        return cls()


@pytest.fixture
def fake_gigaam(monkeypatch, fake_ml):
    FakeAsrModel.calls = []
    FakeAsrModel.raises = None

    gigaam = types.ModuleType("gigaam")
    gigaam.load_model = FakeAsrModel.load_model
    monkeypatch.setitem(sys.modules, "gigaam", gigaam)
    return FakeAsrModel


def test_asr_cache_detection(settings) -> None:
    assert is_asr_model_cached(settings) is False

    path = asr_checkpoint_path(settings)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"ckpt")

    assert is_asr_model_cached(settings) is True


def test_asr_short_name_resolves_to_the_v3_checkpoint(settings) -> None:
    short = settings.model_copy(update={"asr_model": "e2e_rnnt"})

    assert asr_checkpoint_path(short).name == "v3_e2e_rnnt.ckpt"


def test_asr_model_uses_the_configured_name_and_download_root(settings, fake_gigaam) -> None:
    models.load_asr_model(settings)

    call = fake_gigaam.calls[0]
    assert call["model_name"] == settings.asr_model
    assert call["download_root"] == str(settings.models_dir / "gigaam")
    assert call["device"] == "device:cpu"


def test_asr_model_name_is_configurable(settings, fake_gigaam) -> None:
    other = settings.model_copy(update={"asr_model": "v3_rnnt"})

    models.load_asr_model(other)

    assert fake_gigaam.calls[0]["model_name"] == "v3_rnnt"


def test_asr_model_is_loaded_once_per_process(settings, fake_gigaam) -> None:
    first = models.get_asr_model(settings)
    second = models.get_asr_model(settings)

    assert first is second
    assert len(fake_gigaam.calls) == 1


def test_asr_load_failure_has_a_stable_code(settings, fake_gigaam) -> None:
    fake_gigaam.raises = RuntimeError("checksum failed")

    with pytest.raises(AppError) as exc:
        models.load_asr_model(settings)

    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE


def test_asr_without_gigaam_installed_is_reported(settings, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "gigaam", None)

    with pytest.raises(AppError) as exc:
        models.load_asr_model(settings)

    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE
