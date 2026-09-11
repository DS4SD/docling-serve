from unittest.mock import patch

from docling_serve import orchestrator_factory as factory_module
from docling_serve.settings import AsyncEngine


class _CapturedConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _CapturedConverterManagerConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeConverterManager:
    def __init__(self, config):
        self.config = config


class _FakeRayOrchestrator:
    def __init__(self, config, converter_manager):
        self.config = config
        self.converter_manager = converter_manager


def _build_ray_orchestrator(
    monkeypatch, *, max_concurrent_tasks, max_page_slice_parallelism
):
    factory_module.get_async_orchestrator.cache_clear()

    monkeypatch.setattr(
        factory_module.docling_serve_settings, "eng_kind", AsyncEngine.RAY
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "eng_ray_max_concurrent_tasks",
        max_concurrent_tasks,
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "eng_ray_max_page_slice_parallelism",
        max_page_slice_parallelism,
    )

    with (
        patch(
            "docling_jobkit.convert.manager.DoclingConverterManagerConfig",
            _CapturedConverterManagerConfig,
        ),
        patch(
            "docling_jobkit.convert.manager.DoclingConverterManager",
            _FakeConverterManager,
        ),
        patch(
            "docling_jobkit.orchestrators.ray.config.RayOrchestratorConfig",
            _CapturedConfig,
        ),
        patch(
            "docling_jobkit.orchestrators.ray.orchestrator.RayOrchestrator",
            _FakeRayOrchestrator,
        ),
    ):
        return factory_module.get_async_orchestrator()


def test_slice_parallelism_defaults_to_max_concurrent_tasks(monkeypatch):
    orchestrator = _build_ray_orchestrator(
        monkeypatch,
        max_concurrent_tasks=8,
        max_page_slice_parallelism=None,
    )

    assert orchestrator.config.max_page_slice_parallelism == 8


def test_slice_parallelism_preserves_explicit_override(monkeypatch):
    orchestrator = _build_ray_orchestrator(
        monkeypatch,
        max_concurrent_tasks=8,
        max_page_slice_parallelism=3,
    )

    assert orchestrator.config.max_page_slice_parallelism == 3


def test_ray_config_never_receives_none_slice_parallelism(monkeypatch):
    orchestrator = _build_ray_orchestrator(
        monkeypatch,
        max_concurrent_tasks=5,
        max_page_slice_parallelism=None,
    )

    assert orchestrator.config.max_page_slice_parallelism is not None


def test_ray_config_omits_presigned_storage_when_disabled(monkeypatch):
    orchestrator = _build_ray_orchestrator(
        monkeypatch,
        max_concurrent_tasks=5,
        max_page_slice_parallelism=None,
    )

    assert orchestrator.config.presigned_config is None


def test_ray_config_passes_presigned_storage_when_enabled(monkeypatch):
    factory_module.get_async_orchestrator.cache_clear()

    monkeypatch.setattr(
        factory_module.docling_serve_settings, "eng_kind", AsyncEngine.RAY
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "eng_ray_max_concurrent_tasks",
        5,
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "eng_ray_max_page_slice_parallelism",
        None,
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_enabled",
        True,
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_endpoint",
        "s3.example.com",
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_region",
        "us-east-2",
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_verify_ssl",
        False,
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_bucket",
        "bucket-a",
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_access_key",
        "key-a",
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_secret_key",
        "secret-a",
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_key_prefix",
        "converted/test/",
    )
    monkeypatch.setattr(
        factory_module.docling_serve_settings,
        "artifact_storage_presign_ttl_seconds",
        900,
    )

    with (
        patch(
            "docling_jobkit.convert.manager.DoclingConverterManagerConfig",
            _CapturedConverterManagerConfig,
        ),
        patch(
            "docling_jobkit.convert.manager.DoclingConverterManager",
            _FakeConverterManager,
        ),
        patch(
            "docling_jobkit.orchestrators.ray.config.RayOrchestratorConfig",
            _CapturedConfig,
        ),
        patch(
            "docling_jobkit.orchestrators.ray.orchestrator.RayOrchestrator",
            _FakeRayOrchestrator,
        ),
    ):
        orchestrator = factory_module.get_async_orchestrator()

    config = orchestrator.config.presigned_config
    assert config is not None
    assert config.s3_coords.endpoint == "s3.example.com"
    assert config.s3_coords.region == "us-east-2"
    assert config.s3_coords.verify_ssl is False
    assert config.s3_coords.bucket == "bucket-a"
    assert config.s3_coords.access_key == "key-a"
    assert config.s3_coords.secret_key == "secret-a"
    assert config.s3_coords.key_prefix == "converted/test/"
    assert config.url_expiration == 900
