from types import SimpleNamespace
from typing import Annotated, Literal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from docling.datamodel.extraction_options import ExtractionVlmOptions
from docling.datamodel.service.options import (
    ConvertDocumentsOptions,
    ExtractDocumentsOptions,
)
from docling.datamodel.service.requests import (
    AzureBlobSourceRequest,
    BatchConvertSourcesRequest,
    ConvertSourcesRequest,
    ExtractSourcesRequest,
    FileSourceRequest,
    GoogleCloudStorageSourceRequest,
    GoogleDriveSourceRequest,
    HttpSourceRequest,
    S3SourceRequest,
)
from docling.datamodel.service.targets import (
    AzureBlobTarget,
    GoogleCloudStorageTarget,
    GoogleDriveTarget,
    InBodyTarget,
    PresignedUrlTarget,
    S3Target,
)
from docling.datamodel.service.tasks import TaskType
from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions
from docling.models.inference_engines.vlm.base import VlmEngineType
from docling_jobkit.connectors.connector_factory import SourceConnectorFactory
from docling_jobkit.connectors.source_processor import BaseSourceProcessor
from docling_jobkit.datamodel.task import Task

from docling_serve.app import create_app
from docling_serve.datamodel.convert import ConvertDocumentsRequestOptions
from docling_serve.orchestrator_factory import get_async_orchestrator
from docling_serve.policy import (
    ALL_SOURCE_TYPES,
    ALL_TARGET_TYPES,
    _source_kinds,
    build_batch_request_model,
    build_extract_request_model,
    build_service_policy,
    normalize_convert_options,
    normalize_request,
    validate_batch_convert_request,
    validate_convert_options,
    validate_convert_request,
    validate_extract_request,
    validate_source_target_pairing,
    validate_target_kind,
)
from docling_serve.settings import (
    AsyncEngine,
    DoclingServeSettings,
    docling_serve_settings,
)


def test_convert_options_shim_points_to_shared_type():
    assert ConvertDocumentsRequestOptions is ConvertDocumentsOptions


def test_page_range_serializes_to_json_array():
    options = ConvertDocumentsOptions(page_range=(2, 5))

    assert options.model_dump(mode="json")["page_range"] == [2, 5]


def test_normalize_convert_options_sets_default_timeout():
    policy = build_service_policy(DoclingServeSettings())

    normalized = normalize_convert_options(ConvertDocumentsOptions(), policy)

    assert normalized.document_timeout == policy.max_document_timeout


def test_normalize_convert_options_placeholder_disables_images():
    policy = build_service_policy(DoclingServeSettings())

    # include_images defaults to True; placeholder must not fail on that default,
    # it should coerce the include_* flags off instead.
    normalized = normalize_convert_options(
        ConvertDocumentsOptions(
            image_export_mode="placeholder", include_page_images=True
        ),
        policy,
    )

    assert normalized.include_images is False
    assert normalized.include_page_images is False


def test_normalize_convert_options_preserves_images_for_non_placeholder():
    policy = build_service_policy(DoclingServeSettings())

    normalized = normalize_convert_options(
        ConvertDocumentsOptions(
            image_export_mode="referenced", include_page_images=True
        ),
        policy,
    )

    assert normalized.include_images is True
    assert normalized.include_page_images is True


def test_build_service_policy_allows_all_target_types_by_default():
    policy = build_service_policy(DoclingServeSettings())

    assert policy.allowed_target_types == ALL_TARGET_TYPES


def test_validate_convert_options_rejects_timeout_above_policy():
    policy = build_service_policy(DoclingServeSettings(max_document_timeout=10))

    with pytest.raises(HTTPException, match="document_timeout exceeds"):
        validate_convert_options(ConvertDocumentsOptions(document_timeout=11), policy)


def test_validate_convert_options_rejects_images_scale_above_policy():
    policy = build_service_policy(DoclingServeSettings(max_images_scale=1.5))

    with pytest.raises(HTTPException, match="images_scale exceeds"):
        validate_convert_options(ConvertDocumentsOptions(images_scale=1.6), policy)


def test_validate_convert_options_allows_all_image_modes_by_default():
    policy = build_service_policy(DoclingServeSettings())

    # All three modes should be allowed by default
    validate_convert_options(
        ConvertDocumentsOptions(image_export_mode="placeholder"), policy
    )
    validate_convert_options(
        ConvertDocumentsOptions(image_export_mode="referenced"), policy
    )
    validate_convert_options(
        ConvertDocumentsOptions(image_export_mode="embedded"), policy
    )


def test_validate_convert_options_rejects_disallowed_image_mode():
    policy = build_service_policy(
        DoclingServeSettings(allowed_image_export_modes=["placeholder", "referenced"])
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_convert_options(
            ConvertDocumentsOptions(image_export_mode="embedded"), policy
        )

    assert exc_info.value.status_code == 422
    assert "image_export_mode 'embedded' is not allowed" in exc_info.value.detail
    assert "placeholder" in exc_info.value.detail
    assert "referenced" in exc_info.value.detail


def test_validate_convert_options_allows_configured_image_mode():
    policy = build_service_policy(
        DoclingServeSettings(allowed_image_export_modes=["placeholder"])
    )

    # Should allow placeholder
    validate_convert_options(
        ConvertDocumentsOptions(image_export_mode="placeholder"), policy
    )

    # Should reject others
    with pytest.raises(HTTPException, match=r"image_export_mode.*not allowed"):
        validate_convert_options(
            ConvertDocumentsOptions(image_export_mode="referenced"), policy
        )


def test_validate_convert_options_allows_images_scale_at_policy_cap():
    policy = build_service_policy(DoclingServeSettings(max_images_scale=2.0))

    validate_convert_options(ConvertDocumentsOptions(images_scale=2.0), policy)


def test_convert_sources_request_rejects_s3_inputs_at_model_layer():
    with pytest.raises(ValidationError):
        ConvertSourcesRequest(
            options=ConvertDocumentsOptions(),
            sources=[
                S3SourceRequest(
                    endpoint="s3.example.com",
                    access_key="key",
                    secret_key="secret",
                    bucket="bucket",
                )
            ],
            target=S3Target(
                endpoint="s3.example.com",
                access_key="key",
                secret_key="secret",
                bucket="bucket",
            ),
        )


def test_normalize_convert_request_preserves_sources_and_target():
    policy = build_service_policy(DoclingServeSettings())
    request = ConvertSourcesRequest(
        options=ConvertDocumentsOptions(document_timeout=None),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=InBodyTarget(),
    )

    normalized = normalize_request(request, policy)

    assert normalized.sources == request.sources
    assert normalized.target == request.target
    assert normalized.options.document_timeout == policy.max_document_timeout


def test_normalize_convert_request_works_for_convert_sources_request():
    policy = build_service_policy(DoclingServeSettings())
    request = ConvertSourcesRequest(
        options=ConvertDocumentsOptions(document_timeout=None),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=InBodyTarget(),
    )

    normalized = normalize_request(request, policy)

    assert isinstance(normalized, ConvertSourcesRequest)
    assert normalized.sources == request.sources
    assert normalized.options.document_timeout == policy.max_document_timeout


def test_validate_convert_request_rejects_presigned_url_when_storage_disabled():
    policy = build_service_policy(DoclingServeSettings(artifact_storage_enabled=False))
    request = ConvertSourcesRequest(
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=PresignedUrlTarget(),
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_convert_request(request, policy)

    assert exc_info.value.status_code == 422
    assert "artifact storage" in exc_info.value.detail.lower()


def test_validate_convert_request_rejects_too_many_sources():
    policy = build_service_policy(DoclingServeSettings(max_sources_per_request=2))
    request = ConvertSourcesRequest(
        sources=[
            HttpSourceRequest(url="https://example.com/a.pdf", headers={}),
            HttpSourceRequest(url="https://example.com/b.pdf", headers={}),
            HttpSourceRequest(url="https://example.com/c.pdf", headers={}),
        ],
        target=InBodyTarget(),
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_convert_request(request, policy)

    assert exc_info.value.status_code == 422
    assert "Too many sources" in exc_info.value.detail


def test_validate_convert_request_allows_presigned_url_when_storage_enabled():
    policy = build_service_policy(DoclingServeSettings(artifact_storage_enabled=True))
    request = ConvertSourcesRequest(
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=PresignedUrlTarget(),
    )

    validate_convert_request(request, policy)


def test_validate_batch_convert_request_rejects_s3_source_with_presigned_target():
    policy = build_service_policy(DoclingServeSettings(artifact_storage_enabled=True))
    request = BatchConvertSourcesRequest(
        sources=[
            S3SourceRequest(
                endpoint="s3.example.com",
                access_key="key",
                secret_key="secret",
                bucket="bucket",
            )
        ],
        target=PresignedUrlTarget(),
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_batch_convert_request(request, policy)

    assert exc_info.value.status_code == 422
    assert "require a storage target" in exc_info.value.detail


def test_validate_batch_convert_request_allows_s3_source_with_s3_target():
    policy = build_service_policy(DoclingServeSettings())
    request = BatchConvertSourcesRequest(
        sources=[
            S3SourceRequest(
                endpoint="s3.example.com",
                access_key="key",
                secret_key="secret",
                bucket="bucket",
            )
        ],
        target=S3Target(
            endpoint="s3.example.com",
            access_key="key",
            secret_key="secret",
            bucket="converted",
        ),
    )

    validate_batch_convert_request(request, policy)


@pytest.mark.parametrize(
    "source",
    [
        AzureBlobSourceRequest(
            account_name="acct",
            container="incoming",
            connection_string="UseDevelopmentStorage=true",
        ),
        GoogleCloudStorageSourceRequest(bucket="incoming"),
        GoogleDriveSourceRequest(
            path_id="folder-123",
            refresh_token="refresh-token",
            credentials_path="/tmp/client-secret.json",
        ),
    ],
)
def test_validate_batch_convert_request_allows_new_expandable_sources_with_storage_target(
    source,
):
    policy = build_service_policy(DoclingServeSettings())
    request = BatchConvertSourcesRequest(
        sources=[source],
        target=S3Target(
            endpoint="s3.example.com",
            access_key="key",
            secret_key="secret",
            bucket="converted",
        ),
    )

    validate_batch_convert_request(request, policy)


def test_validate_target_kind_rejects_disallowed_target():
    policy = build_service_policy(DoclingServeSettings(allowed_target_types=["zip"]))

    with pytest.raises(HTTPException, match="target kind 'inbody' is not allowed"):
        validate_target_kind("inbody", policy)


def test_validate_batch_convert_request_allows_http_source_with_s3_target():
    policy = build_service_policy(DoclingServeSettings())
    request = BatchConvertSourcesRequest(
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=S3Target(
            endpoint="s3.example.com",
            access_key="key",
            secret_key="secret",
            bucket="converted",
        ),
    )

    validate_batch_convert_request(request, policy)


@pytest.mark.parametrize(
    "target",
    [
        AzureBlobTarget(
            account_name="acct",
            container="converted",
            connection_string="UseDevelopmentStorage=true",
        ),
        GoogleCloudStorageTarget(bucket="converted"),
        GoogleDriveTarget(
            path_id="folder-123",
            refresh_token="refresh-token",
            credentials_path="/tmp/client-secret.json",
        ),
    ],
)
def test_validate_convert_request_allows_http_source_with_storage_target(target):
    policy = build_service_policy(DoclingServeSettings())
    request = ConvertSourcesRequest(
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=target,
    )

    validate_convert_request(request, policy)


def test_normalize_batch_convert_request_sets_default_timeout():
    policy = build_service_policy(DoclingServeSettings())
    request = BatchConvertSourcesRequest(
        options=ConvertDocumentsOptions(document_timeout=None),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=PresignedUrlTarget(),
    )

    normalized = normalize_request(request, policy)

    assert isinstance(normalized, BatchConvertSourcesRequest)
    assert normalized.options.document_timeout == policy.max_document_timeout


def test_validate_convert_request_rejects_disallowed_target_type():
    policy = build_service_policy(DoclingServeSettings(allowed_target_types=["zip"]))
    request = ConvertSourcesRequest(
        options=ConvertDocumentsOptions(),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf", headers={})],
        target=InBodyTarget(),
    )

    with pytest.raises(HTTPException, match="target kind 'inbody' is not allowed"):
        validate_convert_request(request, policy)


def test_build_service_policy_allows_all_source_types_by_default():
    policy = build_service_policy(DoclingServeSettings())

    assert policy.allowed_source_types == ALL_SOURCE_TYPES
    assert ALL_SOURCE_TYPES == frozenset(
        {
            "file",
            "http",
            "s3",
            "azure_blob",
            "google_cloud_storage",
            "google_drive",
        }
    )


def test_source_kinds_supports_nested_known_and_generic_union():
    class KnownSource(BaseModel):
        kind: Literal["known"] = "known"

    class GenericSource(BaseModel):
        kind: str

    known = Annotated[KnownSource, Field(discriminator="kind")]
    source = Annotated[known | GenericSource, BeforeValidator(lambda value: value)]

    assert _source_kinds(source) == frozenset({"known"})


@pytest.mark.parametrize("source_kind", ["ftp", "local_path"])
def test_unavailable_allowed_source_type_fails_startup(source_kind):
    with pytest.raises(ValueError, match=rf"allowed_source_types.*{source_kind}"):
        build_service_policy(
            DoclingServeSettings(allowed_source_types=["http", source_kind])
        )


def test_validate_source_target_pairing_allows_expandable_source_with_database_target():
    policy = build_service_policy(DoclingServeSettings())
    request = BatchConvertSourcesRequest(
        options=ConvertDocumentsOptions(),
        sources=[
            AzureBlobSourceRequest(
                account_name="devstoreaccount1",
                connection_string="UseDevelopmentStorage=true",
                container="docs",
            )
        ],
        target=AzureBlobTarget(
            account_name="devstoreaccount1",
            connection_string="UseDevelopmentStorage=true",
            container="results",
        ),
    )

    validate_source_target_pairing(request.sources, request.target, policy)


def test_build_batch_request_model_keeps_target_and_targets_optional():
    policy = build_service_policy(DoclingServeSettings())

    model = build_batch_request_model(policy)

    assert model.model_fields["target"].is_required() is False
    assert model.model_fields["target"].default is None
    assert model.model_fields["targets"].is_required() is False
    assert model.model_fields["targets"].default is None
    assert model.model_json_schema().get("required") == ["sources"]


def test_connector_without_json_schema_fails_startup(monkeypatch):
    from docling_serve import policy as policy_module

    class Unsupported:
        pass

    class BadSchemaSource(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        kind: Literal["bad_schema"] = "bad_schema"
        value: Unsupported

    class BadSchemaProcessor(BaseSourceProcessor):
        @classmethod
        def get_config_types(cls):
            return (BadSchemaSource,)

    factory = SourceConnectorFactory()
    factory.load_from_plugins()
    factory.register(BadSchemaProcessor, "bad_plugin", __name__)
    monkeypatch.setattr(
        policy_module,
        "get_source_connector_factory",
        lambda allow_external_plugins=False: factory,
    )
    policy = build_service_policy(
        DoclingServeSettings(allowed_source_types=["bad_schema"])
    )

    with pytest.raises(ValueError, match=r"bad_schema.*JSON Schema"):
        build_batch_request_model(policy)


@pytest.mark.parametrize("target_kind", ["unknown", "local_path"])
def test_unavailable_allowed_target_type_fails_startup(target_kind):
    with pytest.raises(ValueError, match=rf"allowed_target_types.*{target_kind}"):
        build_service_policy(DoclingServeSettings(allowed_target_types=[target_kind]))


def test_validate_convert_request_accepts_file_even_when_excluded_from_allowed_source_types():
    # allowed_source_types governs storage connectors on the batch endpoint;
    # inline kinds (file, http) are always accepted on the convert endpoint.
    policy = build_service_policy(DoclingServeSettings(allowed_source_types=["http"]))
    request = ConvertSourcesRequest(
        options=ConvertDocumentsOptions(),
        sources=[FileSourceRequest(base64_string="", filename="a.pdf")],
        target=InBodyTarget(),
    )

    # Must not raise even though "file" is absent from allowed_source_types.
    validate_convert_request(request, policy)


def test_validate_batch_convert_request_rejects_disallowed_source_type():
    policy = build_service_policy(DoclingServeSettings(allowed_source_types=["http"]))
    request = BatchConvertSourcesRequest(
        sources=[
            S3SourceRequest(
                endpoint="s3.example.com",
                access_key="key",
                secret_key="secret",
                bucket="bucket",
            )
        ],
        target=S3Target(
            endpoint="s3.example.com",
            access_key="key",
            secret_key="secret",
            bucket="converted",
        ),
    )

    with pytest.raises(HTTPException, match="source kind 's3' is not allowed"):
        validate_batch_convert_request(request, policy)


def test_extract_request_model_has_one_closed_target():
    policy = build_service_policy(DoclingServeSettings())

    model = build_extract_request_model(policy, InBodyTarget())

    assert "targets" not in model.model_fields
    assert model.model_fields["target"].default.kind == "inbody"


def test_extract_policy_rejects_disallowed_preset():
    policy = build_service_policy(
        DoclingServeSettings(allowed_extraction_presets=["nuextract_2b"])
    )
    request = ExtractSourcesRequest(
        options=ExtractDocumentsOptions(
            template="x", extraction_preset="granite_vision_4_1"
        ),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf")],
    )

    with pytest.raises(HTTPException, match="not allowed"):
        validate_extract_request(request, policy)


def test_extract_policy_rejects_custom_config():
    policy = build_service_policy(DoclingServeSettings())
    request = ExtractSourcesRequest(
        options=ExtractDocumentsOptions(
            template="x", extraction_custom_config={"model_spec": {}}
        ),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf")],
    )

    with pytest.raises(HTTPException, match="Custom extraction configuration"):
        validate_extract_request(request, policy)


def test_extract_policy_rejects_remote_engine_when_remote_services_are_disabled():
    custom = ExtractionVlmOptions.from_preset("nuextract_2b").model_copy(
        update={"engine_options": ApiVlmEngineOptions(engine_type=VlmEngineType.API)}
    )
    policy = build_service_policy(
        DoclingServeSettings(allow_custom_extraction_config=True)
    )
    request = ExtractSourcesRequest(
        options=ExtractDocumentsOptions(template="x", extraction_custom_config=custom),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf")],
    )

    with pytest.raises(HTTPException, match="Remote extraction services are disabled"):
        validate_extract_request(request, policy)


def test_extract_policy_rejects_disallowed_default_engine_at_startup():
    with pytest.raises(ValueError, match=r"Extraction engine.*not allowed"):
        build_service_policy(DoclingServeSettings(allowed_extraction_engines=["api"]))


def test_extract_policy_rejects_known_disallowed_format():
    policy = build_service_policy(
        DoclingServeSettings(allowed_extraction_formats=["image"])
    )
    request = ExtractSourcesRequest(
        options=ExtractDocumentsOptions(template="x"),
        sources=[HttpSourceRequest(url="https://example.com/test.pdf")],
    )

    with pytest.raises(HTTPException, match="Input format 'pdf' is not allowed"):
        validate_extract_request(request, policy)


def test_extract_policy_rejects_expandable_inbody():
    policy = build_service_policy(DoclingServeSettings())
    request = ExtractSourcesRequest(
        options=ExtractDocumentsOptions(template="x"),
        sources=[
            S3SourceRequest(
                endpoint="s3.example.com",
                access_key="key",
                secret_key="secret",
                bucket="bucket",
            )
        ],
    )

    with pytest.raises(HTTPException, match="require a storage target"):
        validate_extract_request(request, policy)


def test_extract_policy_allows_expandable_presigned_target():
    policy = build_service_policy(DoclingServeSettings(artifact_storage_enabled=True))
    request = ExtractSourcesRequest(
        options=ExtractDocumentsOptions(template="x"),
        sources=[
            S3SourceRequest(
                endpoint="s3.example.com",
                access_key="key",
                secret_key="secret",
                bucket="bucket",
            )
        ],
        target=PresignedUrlTarget(),
    )

    validate_extract_request(request, policy)


def test_openapi_only_exposes_async_source_extraction():
    paths = create_app().openapi()["paths"]

    assert "/v1/extract/source/async" in paths
    assert "/v1/extract/source" not in paths
    assert "/v1/extract/file" not in paths
    assert "/v1/extract/file/async" not in paths


@pytest.mark.asyncio
async def test_ray_extract_endpoint_enqueues_one_target_with_dict_template(monkeypatch):
    orchestrator = SimpleNamespace(
        enqueue=AsyncMock(
            return_value=Task(task_id="extract-1", task_type=TaskType.EXTRACT)
        ),
        get_queue_position=AsyncMock(return_value=0),
    )
    app = create_app()
    app.dependency_overrides[get_async_orchestrator] = lambda: orchestrator
    monkeypatch.setattr(docling_serve_settings, "eng_kind", AsyncEngine.RAY)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/extract/source/async",
            json={
                "options": {
                    "template": {"invoice": {"total": "number"}},
                    "extraction_preset": "nuextract_2b",
                    "input_channels": "text",
                },
                "sources": [{"kind": "http", "url": "https://example.com/test.md"}],
            },
        )

    assert response.status_code == 200
    request = orchestrator.enqueue.await_args.kwargs
    assert request["task_type"] == TaskType.EXTRACT
    assert request["extract_options"].template == {"invoice": {"total": "number"}}
    assert [target.kind for target in request["targets"]] == ["inbody"]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [AsyncEngine.LOCAL, AsyncEngine.RQ])
async def test_unsupported_engine_rejects_extraction_before_enqueue(
    monkeypatch, engine
):
    monkeypatch.setattr(docling_serve_settings, "eng_kind", engine)
    orchestrator = SimpleNamespace(enqueue=AsyncMock())
    app = create_app()
    app.dependency_overrides[get_async_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/extract/source/async",
            json={
                "options": {"template": "x"},
                "sources": [{"kind": "http", "url": "https://example.com/test.pdf"}],
            },
        )

    assert response.status_code == 501
    assert f"'{engine.value}' engine" in response.json()["detail"]
    orchestrator.enqueue.assert_not_awaited()
