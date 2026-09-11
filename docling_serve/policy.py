from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypeVar, Union, get_args
from urllib.parse import urlparse

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, create_model

from docling.datamodel.base_models import FormatToExtensions
from docling.datamodel.service.options import (
    ConvertDocumentsOptions,
    ExtractDocumentsOptions,
)
from docling.datamodel.service.requests import (
    BaseChunkDocumentsRequest,
    BatchConvertSourcesRequest,
    BatchSourceRequestItem,
    ConvertSourcesRequest,
    ExtractSourcesRequest,
    ExtractTargetRequest,
    KnownBatchSourceRequestItem,
    KnownBatchTargetRequest,
    SourceRequestItem,
    TargetRequest,
)
from docling.datamodel.service.targets import (
    InBodyTarget,
    PresignedUrlTarget,
)
from docling.models.factories import get_ocr_factory
from docling_core.types.doc import ImageRefMode
from docling_jobkit.connectors.connector_factory import (
    SourceConnectorFactory,
    TargetConnectorFactory,
    get_source_connector_factory,
    get_target_connector_factory,
)
from docling_jobkit.convert.extraction_manager import (
    DocumentExtractionManager,
    DocumentExtractionManagerConfig,
)

from docling_serve.settings import DoclingServeSettings


def _models_by_kind(annotation: Any) -> dict[str, type[BaseModel]]:
    """Concrete discriminator models from a possibly nested annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        kind = annotation.model_fields.get("kind")
        return (
            {kind.default: annotation} if kind and isinstance(kind.default, str) else {}
        )

    models: dict[str, type[BaseModel]] = {}
    for member in get_args(annotation):
        models.update(_models_by_kind(member))
    return models


def _source_kinds(annotation: Any) -> frozenset[str]:
    """Discriminator kinds from nested source unions."""
    return frozenset(_models_by_kind(annotation))


# Derived from the endpoint source unions so it can never drift past what the
# request models actually accept; allowed_source_types only ever narrows this.
ALL_SOURCE_TYPES = _source_kinds(SourceRequestItem) | _source_kinds(
    BatchSourceRequestItem
)
# Kinds that are only reachable via the convert/chunk endpoint schema
# (SourceRequestItem). They carry their payload inline in the request body and
# have no storage-connector equivalent, so allowed_source_types — which is
# intended to gate storage connectors on the batch endpoint — must not block
# them on the convert/chunk endpoints.
_INLINE_SOURCE_KINDS = _source_kinds(SourceRequestItem)
ALL_TARGET_TYPES = _source_kinds(TargetRequest)
_ConvertRequestT = TypeVar(
    "_ConvertRequestT", ConvertSourcesRequest, BatchConvertSourcesRequest
)

_REMOTE_EXCLUDED_SOURCE_KINDS = frozenset({"local_path"})
_REMOTE_EXCLUDED_TARGET_KINDS = frozenset({"local_path"})


def validate_source_target_pairing(
    sources: list[Any],
    target: Any,
    policy: ServicePolicy,
    *,
    storage_modes: frozenset[str] = frozenset({"artifacts", "database"}),
) -> None:
    """Reject expandable sources paired with a non-storage target.

    Bucket/drive sources can fan out into many documents, so their results must
    go to a storage target that writes each document out directly. Non-expandable
    sources (file/http) may use any target, including storage targets.
    """
    expandable = sorted(
        {
            source.kind
            for source in sources
            if policy.source_factory.supports(source.kind)
            and policy.source_factory.is_expandable(source)
        }
    )
    result_mode = (
        policy.target_factory.result_mode(target)
        if policy.target_factory.supports(target)
        else None
    )
    # Expandable sources require a storage-style target that can handle one or
    # more outputs per discovered document. Both artifact storage targets and
    # database targets satisfy that requirement.
    if expandable and result_mode not in storage_modes:
        target_kind = target.kind if target is not None else None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"sources of kind {expandable} can expand into multiple documents "
                "and require a storage target with artifact result mode; "
                f"got target kind '{target_kind}'."
            ),
        )


@dataclass(frozen=True, slots=True)
class ServicePolicy:
    max_document_timeout: float
    max_images_scale: float
    allow_external_plugins: bool
    allowed_ocr_presets: frozenset[str]
    allowed_source_types: frozenset[str]
    allowed_target_types: frozenset[str]
    callbacks_enabled: bool
    custom_vlm_enabled: bool
    artifact_storage_enabled: bool
    max_sources_per_request: int
    allowed_image_export_modes: frozenset[str]
    source_factory: SourceConnectorFactory
    target_factory: TargetConnectorFactory
    extraction_manager: DocumentExtractionManager


def _configured_types(
    configured: list[str] | None,
    *,
    defaults: frozenset[str],
    available: frozenset[str],
    setting: str,
) -> frozenset[str]:
    if configured is None:
        return defaults

    requested = frozenset(configured)
    unavailable = requested - available
    if unavailable:
        raise ValueError(f"{setting} contains unavailable kinds: {sorted(unavailable)}")
    return requested


def _artifact_target_models(
    factory: TargetConnectorFactory,
) -> dict[str, type[BaseModel]]:
    """Return registered target kinds with 'artifacts' result mode."""
    return {
        kind: model
        for kind, model in factory.registered_config_types_by_kind.items()
        if kind not in _REMOTE_EXCLUDED_TARGET_KINDS
        and factory.result_mode_for_kind(kind) == "artifacts"
    }


def _database_target_models(
    factory: TargetConnectorFactory,
) -> dict[str, type[BaseModel]]:
    """Return registered target kinds with 'database' result mode."""
    return {
        kind: model
        for kind, model in factory.registered_config_types_by_kind.items()
        if kind not in _REMOTE_EXCLUDED_TARGET_KINDS
        and factory.result_mode_for_kind(kind) == "database"
    }


def _storage_target_models(
    factory: TargetConnectorFactory,
) -> dict[str, type[BaseModel]]:
    """Return all registered non-local target kinds (artifacts + database)."""
    return _artifact_target_models(factory) | _database_target_models(factory)


def _closed_union(models: dict[str, type[BaseModel]]) -> Any:
    members = tuple(models.values())
    if not members:
        return type(None)
    if len(members) == 1:
        return members[0]
    return Annotated[Union[members], Field(discriminator="kind")]  # type: ignore[valid-type]


def build_batch_request_model(
    policy: ServicePolicy,
) -> type[BatchConvertSourcesRequest]:
    """Build the closed batch request model advertised by this deployment."""
    known_batch_sources = _models_by_kind(KnownBatchSourceRequestItem)
    all_known_sources = _models_by_kind(SourceRequestItem) | known_batch_sources
    source_models = {
        kind: model
        for kind, model in known_batch_sources.items()
        if kind in policy.allowed_source_types
    }
    source_models.update(
        {
            kind: model
            for kind, model in policy.source_factory.registered_config_types_by_kind.items()
            if kind in policy.allowed_source_types
            and kind not in all_known_sources
            and kind not in _REMOTE_EXCLUDED_SOURCE_KINDS
        }
    )

    known_batch_targets = _models_by_kind(KnownBatchTargetRequest)
    target_models = {
        kind: model
        for kind, model in known_batch_targets.items()
        if kind in policy.allowed_target_types
    }
    target_models.update(
        {
            kind: model
            for kind, model in _storage_target_models(policy.target_factory).items()
            if kind in policy.allowed_target_types and kind not in ALL_TARGET_TYPES
        }
    )
    for kind, connector_model in (source_models | target_models).items():
        try:
            connector_model.model_json_schema()
        except Exception as exc:
            raise ValueError(
                f"Connector kind {kind!r} cannot produce JSON Schema."
            ) from exc

    source_union = _closed_union(source_models)
    target_union = _closed_union(target_models)
    model = create_model(
        "BatchConvertSourcesRequest",
        __base__=BatchConvertSourcesRequest,
        sources=(list[source_union], Field(min_length=1)),  # type: ignore[valid-type]
        target=(target_union | None, None),
        targets=(list[target_union] | None, None),  # type: ignore[valid-type]
    )
    model.model_json_schema()
    return model


def build_extract_request_model(
    policy: ServicePolicy, default_target: Any
) -> type[ExtractSourcesRequest]:
    """Build the closed extraction request model advertised by this deployment."""
    known_sources = _models_by_kind(KnownBatchSourceRequestItem)
    source_models = {
        kind: model
        for kind, model in known_sources.items()
        if kind in policy.allowed_source_types
    }
    source_models.update(
        {
            kind: model
            for kind, model in policy.source_factory.registered_config_types_by_kind.items()
            if kind in policy.allowed_source_types and kind not in source_models
        }
    )

    target_models = {
        kind: model
        for kind, model in _models_by_kind(ExtractTargetRequest).items()
        if kind in policy.allowed_target_types
    }
    target_models.update(
        {
            kind: model
            for kind, model in _artifact_target_models(policy.target_factory).items()
            if kind in policy.allowed_target_types and kind not in target_models
        }
    )
    source_union = _closed_union(source_models)
    target_union = _closed_union(target_models)
    model = create_model(
        "ExtractSourcesRequest",
        __base__=ExtractSourcesRequest,
        sources=(list[source_union], Field(min_length=1)),  # type: ignore[valid-type]
        target=(target_union, default_target),
    )
    model.model_json_schema()
    return model


def build_service_policy(settings: DoclingServeSettings) -> ServicePolicy:
    ocr_factory = get_ocr_factory(
        allow_external_plugins=settings.allow_external_plugins
    )
    registered_ocr_presets = {str(kind) for kind in ocr_factory.registered_kind}
    if settings.allowed_ocr_presets is None:
        allowed_ocr_presets = registered_ocr_presets
    else:
        allowed_ocr_presets = set(settings.allowed_ocr_presets) & registered_ocr_presets
    source_factory = get_source_connector_factory(settings.allow_external_plugins)
    target_factory = get_target_connector_factory(settings.allow_external_plugins)
    available_source_types = (
        ALL_SOURCE_TYPES | frozenset(source_factory.registered_kinds)
    ) - _REMOTE_EXCLUDED_SOURCE_KINDS
    available_target_types = (
        ALL_TARGET_TYPES | frozenset(_storage_target_models(target_factory))
    ) - _REMOTE_EXCLUDED_TARGET_KINDS
    allowed_source_types = _configured_types(
        settings.allowed_source_types,
        defaults=ALL_SOURCE_TYPES,
        available=available_source_types,
        setting="allowed_source_types",
    )
    allowed_target_types = _configured_types(
        settings.allowed_target_types,
        defaults=ALL_TARGET_TYPES,
        available=available_target_types,
        setting="allowed_target_types",
    )

    # Determine allowed image export modes
    if settings.allowed_image_export_modes is None:
        # All modes allowed by default
        allowed_image_export_modes = {"placeholder", "referenced", "embedded"}
    else:
        # Validate that only known modes are specified
        valid_modes = {"placeholder", "referenced", "embedded"}
        allowed_image_export_modes = (
            set(settings.allowed_image_export_modes) & valid_modes
        )

    extraction_manager = DocumentExtractionManager(
        DocumentExtractionManagerConfig(
            artifacts_path=settings.artifacts_path,
            options_cache_size=settings.options_cache_size,
            enable_remote_services=settings.enable_remote_services,
            allow_external_plugins=settings.allow_external_plugins,
            max_num_pages=settings.max_num_pages,
            max_file_size=settings.max_file_size,
            allowed_formats=settings.allowed_extraction_formats,
            default_extraction_preset=settings.default_extraction_preset,
            allowed_extraction_presets=settings.allowed_extraction_presets,
            allow_custom_extraction_config=settings.allow_custom_extraction_config,
            allowed_extraction_engines=settings.allowed_extraction_engines,
        )
    )
    extraction_manager.resolve_extraction_model(ExtractDocumentsOptions(template={}))

    return ServicePolicy(
        max_document_timeout=settings.max_document_timeout,
        max_images_scale=settings.max_images_scale,
        allow_external_plugins=settings.allow_external_plugins,
        allowed_ocr_presets=frozenset(allowed_ocr_presets),
        allowed_source_types=allowed_source_types,
        allowed_target_types=allowed_target_types,
        callbacks_enabled=True,
        custom_vlm_enabled=settings.allow_custom_vlm_config,
        artifact_storage_enabled=settings.artifact_storage_enabled,
        max_sources_per_request=settings.max_sources_per_request,
        allowed_image_export_modes=frozenset(allowed_image_export_modes),
        source_factory=source_factory,
        target_factory=target_factory,
        extraction_manager=extraction_manager,
    )


def resolve_default_target(policy: ServicePolicy) -> TargetRequest:
    """Pick a target for requests that omit one.

    Clients drop fields left at their model default, so an omitted ``target``
    arrives carrying whatever default the request model happens to declare —
    which may not be a target this deployment allows, falsely tripping the
    policy checks with a 422. Resolve the omitted case to something the
    deployment actually supports instead: prefer presigned whenever artifact
    storage is enabled (keeps result payloads out of the response body), and
    otherwise fall back to inbody. If inbody is not allowed either the request
    is misconfigured; return it anyway and let validation surface a clear error.
    """
    if (
        "presigned_url" in policy.allowed_target_types
        and policy.artifact_storage_enabled
    ):
        return PresignedUrlTarget()
    return InBodyTarget()


def normalize_convert_options(
    options: ConvertDocumentsOptions, policy: ServicePolicy
) -> ConvertDocumentsOptions:
    updates: dict[str, Any] = {}

    if options.document_timeout is None:
        updates["document_timeout"] = policy.max_document_timeout

    # Placeholder export mode discards all image data, so generating images would
    # be wasted work. Coerce the include_* flags off rather than rejecting the
    # request: include_images defaults to True, so a 422 here would fail requests
    # purely on a default the caller never set.
    if options.image_export_mode == ImageRefMode.PLACEHOLDER:
        if options.include_images:
            updates["include_images"] = False
        if options.include_page_images:
            updates["include_page_images"] = False

    if not updates:
        return options
    return options.model_copy(update=updates, deep=True)


def normalize_request(
    request: _ConvertRequestT, policy: ServicePolicy
) -> _ConvertRequestT:
    return request.model_copy(
        update={"options": normalize_convert_options(request.options, policy)},
        deep=True,
    )


def validate_convert_options(
    options: ConvertDocumentsOptions, policy: ServicePolicy
) -> None:
    if options.document_timeout is not None:
        if options.document_timeout <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="document_timeout must be greater than 0.",
            )
        if options.document_timeout > policy.max_document_timeout:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "document_timeout exceeds the configured maximum "
                    f"of {policy.max_document_timeout} seconds."
                ),
            )
    if options.images_scale > policy.max_images_scale:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "images_scale exceeds the configured maximum "
                f"of {policy.max_images_scale}."
            ),
        )

    if options.ocr_preset not in policy.allowed_ocr_presets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"ocr_preset '{options.ocr_preset}' is not allowed. "
                f"Allowed values: {sorted(policy.allowed_ocr_presets)}."
            ),
        )

    image_export_mode = getattr(
        options.image_export_mode, "value", options.image_export_mode
    )
    if image_export_mode not in policy.allowed_image_export_modes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"image_export_mode '{image_export_mode}' is not allowed. "
                f"Allowed values: {sorted(policy.allowed_image_export_modes)}."
            ),
        )

    if options.vlm_pipeline_custom_config and not policy.custom_vlm_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Custom VLM configuration is disabled by server policy.",
        )


def validate_target_kind(target_kind: str, policy: ServicePolicy) -> None:
    if target_kind in policy.allowed_target_types:
        return

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"target kind '{target_kind}' is not allowed. "
            f"Allowed values: {sorted(policy.allowed_target_types)}."
        ),
    )


def validate_source_kinds(
    sources: Any,
    policy: ServicePolicy,
    *,
    skip_kinds: frozenset[str] = frozenset(),
) -> None:
    for source in sources:
        if source.kind in skip_kinds:
            continue
        if source.kind not in policy.allowed_source_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"source kind '{source.kind}' is not allowed. "
                    f"Allowed values: {sorted(policy.allowed_source_types)}."
                ),
            )


def validate_convert_request(
    request: ConvertSourcesRequest, policy: ServicePolicy
) -> None:
    validate_convert_options(request.options, policy)
    # Inline kinds (file, http) are intrinsic to the convert/chunk endpoint
    # schemas and must always be accepted there; allowed_source_types governs
    # storage connectors on the batch endpoint.
    validate_source_kinds(request.sources, policy, skip_kinds=_INLINE_SOURCE_KINDS)
    validate_target_kind(request.target.kind, policy)

    if request.callbacks and not policy.callbacks_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Callbacks are disabled by server policy.",
        )

    if len(request.sources) > policy.max_sources_per_request:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Too many sources: {len(request.sources)} exceeds the "
                f"maximum of {policy.max_sources_per_request}."
            ),
        )

    if isinstance(request.target, PresignedUrlTarget):
        if not policy.artifact_storage_enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Presigned URL target requires artifact storage to be configured "
                    "and enabled on the server."
                ),
            )

    validate_source_target_pairing(request.sources, request.target, policy)


def validate_batch_convert_request(
    request: BatchConvertSourcesRequest, policy: ServicePolicy
) -> None:
    validate_convert_options(request.options, policy)
    validate_source_kinds(request.sources, policy)

    if request.callbacks and not policy.callbacks_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Callbacks are disabled by server policy.",
        )

    if len(request.sources) > policy.max_sources_per_request:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Too many sources: {len(request.sources)} exceeds the "
                f"maximum of {policy.max_sources_per_request}."
            ),
        )

    # Resolve the effective target list: `targets` takes precedence over the
    # singular `target` convenience field; fall back to a single-item list.
    effective_targets: list[Any] = request.targets or (
        [request.target] if request.target is not None else []
    )

    for t in effective_targets:
        if isinstance(t, PresignedUrlTarget):
            if not policy.artifact_storage_enabled:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        "Presigned URL target requires artifact storage to be configured "
                        "and enabled on the server."
                    ),
                )

    for t in effective_targets:
        validate_source_target_pairing(request.sources, t, policy)


def validate_extract_request(
    request: ExtractSourcesRequest, policy: ServicePolicy
) -> None:
    validate_source_kinds(request.sources, policy)
    validate_target_kind(request.target.kind, policy)
    allowed_formats = policy.extraction_manager.config.allowed_formats
    if allowed_formats is not None:
        for source in request.sources:
            if policy.source_factory.is_expandable(source):
                continue
            name = getattr(source, "filename", None)
            if name is None and (url := getattr(source, "url", None)) is not None:
                name = urlparse(str(url)).path
            if not name:
                continue
            lower_name = str(name).lower()
            detected = next(
                (
                    fmt
                    for fmt, extensions in FormatToExtensions.items()
                    if any(lower_name.endswith(f".{ext.lower()}") for ext in extensions)
                ),
                None,
            )
            if detected is not None and detected not in allowed_formats:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Input format '{detected.value}' is not allowed for extraction. "
                        f"Allowed values: {sorted(fmt.value for fmt in allowed_formats)}."
                    ),
                )
    if request.callbacks and not policy.callbacks_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Callbacks are disabled by server policy.",
        )
    if len(request.sources) > policy.max_sources_per_request:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Too many sources: {len(request.sources)} exceeds the "
                f"maximum of {policy.max_sources_per_request}."
            ),
        )
    if (
        isinstance(request.target, PresignedUrlTarget)
        and not policy.artifact_storage_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Presigned URL target requires artifact storage to be configured "
                "and enabled on the server."
            ),
        )
    if (
        policy.target_factory.supports(request.target)
        and policy.target_factory.result_mode(request.target) == "database"
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Database/vector targets are not supported for extraction.",
        )
    try:
        policy.extraction_manager.resolve_extraction_model(request.options)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    validate_source_target_pairing(
        request.sources,
        request.target,
        policy,
        storage_modes=frozenset({"artifacts", "presigned"}),
    )


def validate_chunk_request(
    request: BaseChunkDocumentsRequest, policy: ServicePolicy
) -> None:
    validate_convert_options(request.convert_options, policy)
    validate_source_kinds(request.sources, policy, skip_kinds=_INLINE_SOURCE_KINDS)
    validate_target_kind(request.target.kind, policy)

    if request.callbacks and not policy.callbacks_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Callbacks are disabled by server policy.",
        )

    if isinstance(request.target, PresignedUrlTarget):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="presigned_url target is not supported for chunk endpoints.",
        )

    validate_source_target_pairing(request.sources, request.target, policy)
