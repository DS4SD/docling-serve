import asyncio
import copy
import gc
import hashlib
import importlib.metadata
import logging
import os
import shutil
import time
from collections import Counter
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Annotated, Any

import psutil
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import create_model
from scalar_fastapi import get_scalar_api_reference

from docling.datamodel.base_models import DocumentStream
from docling.datamodel.service.callbacks import (
    CallbackSpec,
    ProgressCallbackRequest,
    ProgressCallbackResponse,
)
from docling.datamodel.service.chunking import (
    BaseChunkerOptions,
    HierarchicalChunkerOptions,
    HybridChunkerOptions,
)
from docling.datamodel.service.options import (
    ConvertDocumentsOptions as ConvertDocumentsRequestOptions,
)
from docling.datamodel.service.requests import (
    BatchConvertSourcesRequest,
    ConvertSourcesRequest,
    ExtractSourcesRequest,
    GenericChunkDocumentsRequest,
    TargetName,
    TargetRequest,
    make_request_model,
)
from docling.datamodel.service.responses import (
    ChunkDocumentResponse,
    ClearResponse,
    ConvertDocumentResponse,
    ExtractDocumentResponse,
    HealthCheckResponse,
    MessageKind,
    PresignedUrlConvertDocumentResponse,
    PresignedUrlConvertResponse,
    ReadinessResponse,
    TaskFailureResult,
    TaskStatusResponse,
    WebsocketMessage,
)
from docling.datamodel.service.targets import (
    InBodyTarget,
    PresignedUrlTarget,
    ZipTarget,
)
from docling.datamodel.service.tasks import TaskType
from docling_jobkit.connectors.errors import (
    SourceConnectorConfigError,
    TargetConnectorConfigError,
)
from docling_jobkit.datamodel.chunking import ChunkingExportOptions
from docling_jobkit.datamodel.stored_outcome import (
    StoredFailureOutcome,
    StoredSuccessOutcome,
)
from docling_jobkit.datamodel.task import Task, TaskSource
from docling_jobkit.orchestrators.base_orchestrator import (
    BaseOrchestrator,
    ProgressInvalid,
    RedisBackpressureError,
    TaskNotFoundError,
)
from docling_jobkit.orchestrators.rq.orchestrator import RQOrchestrator

from docling_serve.auth import APIKeyAuth, AuthenticationResult
from docling_serve.helper_functions import (
    DOCLING_VERSIONS,
    FormDepends,
    parse_callback_item,
)
from docling_serve.logging_config import setup_logging
from docling_serve.orchestrator_factory import get_async_orchestrator
from docling_serve.otel_instrumentation import (
    get_metrics_endpoint_content,
    setup_otel_instrumentation,
)
from docling_serve.policy import (
    build_batch_request_model,
    build_extract_request_model,
    build_service_policy,
    normalize_convert_options,
    normalize_request,
    resolve_default_target,
    validate_batch_convert_request,
    validate_chunk_request,
    validate_convert_options,
    validate_convert_request,
    validate_extract_request,
    validate_target_kind,
)
from docling_serve.public_errors import build_public_http_detail
from docling_serve.response_preparation import prepare_response
from docling_serve.settings import AsyncEngine, docling_serve_settings
from docling_serve.storage import get_scratch
from docling_serve.websocket_notifier import WebsocketNotifier

# Pre-import OCR backends that use cysignals (signal handlers must be registered
# in the main thread; worker threads would raise "signal only works in main thread").
try:
    import tesserocr  # noqa: F401
except (ImportError, Exception):
    pass


# Configure logging based on settings
# This will be called early, but can be reconfigured in __main__.py
log_level = (
    docling_serve_settings.log_level.value
    if docling_serve_settings.log_level
    else "INFO"
)
setup_logging(
    log_format=docling_serve_settings.log_format.value,
    log_level=log_level,
    header_prefix=docling_serve_settings.log_header_prefix,
)

_log = logging.getLogger(__name__)

# Tracks whether warm_up_caches() has completed.  Meaningful only for the
# LocalOrchestrator (which eagerly loads ML models); the RQ orchestrator's
# implementation is a no-op so this event fires instantly in RQ deployments.
_models_ready = asyncio.Event()

# Set if the background queue processor task dies with an error. Liveness/
# readiness then fail so the platform restarts the pod instead of silently
# serving with a dead orchestrator loop: a dead pub/sub listener stops WebSocket
# push delivery while polling still succeeds, which is otherwise very hard to
# detect.
_queue_processor_failed = asyncio.Event()


def _supervise_queue_processor(task: asyncio.Task, failed_event: asyncio.Event) -> None:
    """Mark the orchestrator loop unhealthy only if it died with an exception.

    A clean return is legitimate: some engines (e.g. KFP) have no in-process
    queue loop and ``process_queue()`` is a no-op, so completing is expected and
    must not flag the pod unhealthy. Only an unhandled exception means a
    supervised loop (RQ/Ray pub/sub listener, Local workers) actually broke.
    """
    if task.cancelled():
        return  # expected on shutdown
    exc = task.exception()
    if exc is None:
        _log.debug("Background queue processor completed without error")
        return
    _log.error("Background queue processor died: %s", exc, exc_info=exc)
    failed_event.set()


# Context manager to initialize and clean up the lifespan of the FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    scratch_dir = get_scratch()

    orchestrator = get_async_orchestrator()
    notifier = WebsocketNotifier(orchestrator)
    orchestrator.bind_notifier(notifier)

    # Warm up processing cache (loads ML models for LocalOrchestrator;
    # no-op for RQOrchestrator since models live in the worker pods).
    if docling_serve_settings.load_models_at_boot:
        await orchestrator.warm_up_caches()

    _models_ready.set()

    # Start the background queue processor. If a supervised loop (RQ/Ray pub/sub
    # listener, Local workers) ever crashes, the done-callback flags the pod
    # unhealthy so it gets restarted instead of silently dropping WebSocket push.
    queue_task = asyncio.create_task(orchestrator.process_queue())
    queue_task.add_done_callback(
        lambda task: _supervise_queue_processor(task, _queue_processor_failed)
    )

    reaper_task = None
    if isinstance(orchestrator, RQOrchestrator):
        reaper_task = asyncio.create_task(orchestrator._reap_zombie_tasks())

    yield

    # Cancel the background queue processor on shutdown
    queue_task.cancel()
    if reaper_task:
        reaper_task.cancel()
    try:
        await queue_task
    except asyncio.CancelledError:
        _log.info("Queue processor cancelled.")
    if reaper_task:
        try:
            await reaper_task
        except asyncio.CancelledError:
            _log.info("Zombie reaper cancelled.")

    # Remove scratch directory in case it was a tempfile
    if docling_serve_settings.scratch_path is not None:
        shutil.rmtree(scratch_dir, ignore_errors=True)


##################################
# App creation and configuration #
##################################


def create_app():  # noqa: C901
    try:
        version = importlib.metadata.version("docling_serve")
    except importlib.metadata.PackageNotFoundError:
        _log.warning("Unable to get docling_serve version, falling back to 0.0.0")

        version = "0.0.0"

    offline_docs_assets = False
    if (
        docling_serve_settings.static_path is not None
        and (docling_serve_settings.static_path).is_dir()
    ):
        offline_docs_assets = True
        _log.info("Found static assets.")

    require_auth = APIKeyAuth(docling_serve_settings.api_key)
    service_policy = build_service_policy(docling_serve_settings)

    # Clients omit fields left at their model default, so the imported request
    # model's `target` default must match what this deployment actually accepts;
    # otherwise an omitted target arrives carrying a target kind the policy
    # rejects with a spurious 422. Subclass the imported model to repopulate the
    # `target` default with the deployment-resolved one. Because the default is
    # a concrete value it also flows into the OpenAPI schema automatically. The
    # subclass keeps the public "ConvertSourcesRequest" schema name.
    default_target = resolve_default_target(service_policy)
    default_target_name = TargetName(default_target.kind)
    ConvertSourcesRequestModel = create_model(
        "ConvertSourcesRequest",
        __base__=ConvertSourcesRequest,
        target=(TargetRequest, default_target),
    )
    BatchConvertSourcesRequestModel = build_batch_request_model(service_policy)
    ExtractSourcesRequestModel = build_extract_request_model(
        service_policy, default_target
    )

    app = FastAPI(
        title="Docling Serve",
        docs_url=None if offline_docs_assets else "/swagger",
        redoc_url=None if offline_docs_assets else "/docs",
        lifespan=lifespan,
        version=version,
    )

    @app.exception_handler(RedisBackpressureError)
    async def redis_backpressure_error_handler(
        request: Request, exc: RedisBackpressureError
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Server is busy, please try again shortly."},
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        errors = [
            {
                key: value
                for key, value in error.items()
                if key in {"loc", "msg", "type"}
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": errors},
        )

    if docling_serve_settings.eng_kind == AsyncEngine.RAY:
        from docling_jobkit.orchestrators.ray.orchestrator import (
            DispatcherUnavailableError,
        )

        @app.exception_handler(DispatcherUnavailableError)
        async def dispatcher_unavailable_error_handler(
            request: Request, exc: Exception
        ) -> JSONResponse:
            del request
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": build_public_http_detail(
                        exc=exc,
                        debug_enabled=docling_serve_settings.debug_error_details,
                        fallback_message="Ray dispatcher is unavailable.",
                    )
                },
                headers={"Retry-After": "1"},
            )

    # Setup OpenTelemetry instrumentation
    redis_url = (
        docling_serve_settings.eng_rq_redis_url
        if docling_serve_settings.eng_kind == AsyncEngine.RQ
        else None
    )

    # Get Ray redis_manager if using Ray engine
    ray_redis_manager = None
    if docling_serve_settings.eng_kind == AsyncEngine.RAY:
        from docling_jobkit.orchestrators.ray.orchestrator import RayOrchestrator

        orchestrator = get_async_orchestrator()
        assert isinstance(orchestrator, RayOrchestrator)
        ray_redis_manager = orchestrator.redis_manager

    setup_otel_instrumentation(
        app,
        service_name=docling_serve_settings.otel_service_name,
        enable_metrics=docling_serve_settings.otel_enable_metrics,
        enable_traces=docling_serve_settings.otel_enable_traces,
        enable_prometheus=docling_serve_settings.otel_enable_prometheus,
        enable_otlp_metrics=docling_serve_settings.otel_enable_otlp_metrics,
        redis_url=redis_url,
        metrics_port=docling_serve_settings.metrics_port,
        ray_redis_manager=ray_redis_manager,
    )

    # Add log context middleware to extract request headers
    from docling_serve.logging_config import LogContextMiddleware

    app.add_middleware(
        LogContextMiddleware,
        header_prefix=docling_serve_settings.log_header_prefix,
    )

    origins = docling_serve_settings.cors_origins
    methods = docling_serve_settings.cors_methods
    headers = docling_serve_settings.cors_headers

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=methods,
        allow_headers=headers,
    )

    # Mount the Gradio app
    if docling_serve_settings.enable_ui:
        try:
            import gradio as gr

            from docling_serve.gradio_ui import ui as gradio_ui
            from docling_serve.settings import uvicorn_settings

            tmp_output_dir = get_scratch() / "gradio"
            tmp_output_dir.mkdir(exist_ok=True, parents=True)
            gradio_ui.gradio_output_dir = tmp_output_dir

            # Build the root_path for Gradio, accounting for UVICORN_ROOT_PATH
            gradio_root_path = (
                f"{uvicorn_settings.root_path}/ui"
                if uvicorn_settings.root_path
                else "/ui"
            )

            app = gr.mount_gradio_app(
                app,
                gradio_ui,
                path="/ui",
                allowed_paths=["./logo.png", tmp_output_dir],
                root_path=gradio_root_path,
            )
        except ImportError:
            _log.warning(
                "Docling Serve enable_ui is activated, but gradio is not installed. "
                "Install it with `pip install docling-serve[ui]` "
                "or `pip install gradio`"
            )

    #############################
    # Offline assets definition #
    #############################
    if offline_docs_assets:
        app.mount(
            "/static",
            StaticFiles(directory=docling_serve_settings.static_path),
            name="static",
        )

        @app.get("/swagger", include_in_schema=False)
        async def custom_swagger_ui_html():
            return get_swagger_ui_html(
                openapi_url=app.openapi_url,
                title=app.title + " - Swagger UI",
                oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
                swagger_js_url="/static/swagger-ui-bundle.js",
                swagger_css_url="/static/swagger-ui.css",
            )

        @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
        async def swagger_ui_redirect():
            return get_swagger_ui_oauth2_redirect_html()

        @app.get("/docs", include_in_schema=False)
        async def redoc_html():
            return get_redoc_html(
                openapi_url=app.openapi_url,
                title=app.title + " - ReDoc",
                redoc_js_url="/static/redoc.standalone.js",
            )

    @app.get("/scalar", include_in_schema=False)
    async def scalar_html():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
            scalar_favicon_url="https://raw.githubusercontent.com/docling-project/docling/refs/heads/main/docs/assets/logo.svg",
            # hide_client_button=True,  # not yet released but in main
        )

    ########################
    # Async / Sync helpers #
    ########################

    async def _enqueue_source(
        orchestrator: BaseOrchestrator,
        request: (
            BatchConvertSourcesRequest
            | ConvertSourcesRequest
            | GenericChunkDocumentsRequest
        ),
        tenant_id: str | None = None,
    ) -> Task:
        sources: list[TaskSource]
        enqueue_targets: list[Any]
        try:
            # Normalize every source to its concrete, kind-bearing registry model so
            # it survives the internal Task resolver the orchestrator runs at enqueue.
            # Non-batch file/http requests are registered connectors too, so they take
            # the same path — stripping them to kind-less FileSource/HttpSource here
            # would make that resolver reject them. InBody/Zip targets are left as-is
            # (the Task resolver handles those); only batch normalizes its storage
            # target through the registry.
            sources = [
                service_policy.source_factory.validate_config(source)
                for source in request.sources
            ]
            if isinstance(request, BatchConvertSourcesRequest):
                # Resolve effective targets: `targets` list takes precedence over the
                # singular `target` convenience field.
                raw_targets = request.targets or (
                    [request.target] if request.target is not None else []
                )
                enqueue_targets = [
                    service_policy.target_factory.validate_config(t)
                    for t in raw_targets
                ]
            else:
                assert request.target is not None, (
                    "target must be set before enqueueing"
                )
                enqueue_targets = [request.target]
        except (SourceConnectorConfigError, TargetConnectorConfigError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        convert_options: ConvertDocumentsRequestOptions
        chunking_options: BaseChunkerOptions | None = None
        chunking_export_options = ChunkingExportOptions()
        task_type: TaskType
        if isinstance(request, BatchConvertSourcesRequest | ConvertSourcesRequest):
            task_type = TaskType.CONVERT
            convert_options = request.options
        elif isinstance(request, GenericChunkDocumentsRequest):
            task_type = TaskType.CHUNK
            convert_options = request.convert_options
            chunking_options = request.chunking_options
            chunking_export_options.include_converted_doc = (
                request.include_converted_doc
            )
        else:
            raise RuntimeError("Uknown request type.")

        # Prepare metadata with tenant_id BEFORE enqueueing
        # This is critical because ray orchestrator reads tenant_id during enqueue()
        task_metadata: dict[str, str] = {}
        if tenant_id:
            task_metadata["tenant_id"] = tenant_id
            _log.info(
                f"[TENANT_ID] Preparing to enqueue with tenant_id='{tenant_id}' in metadata"
            )
        else:
            _log.warning("[TENANT_ID] No tenant_id provided, will use default")

        task = await orchestrator.enqueue(
            task_type=task_type,
            sources=sources,
            convert_options=convert_options,
            chunking_options=chunking_options,
            chunking_export_options=chunking_export_options,
            targets=enqueue_targets,
            callbacks=request.callbacks,
            metadata=task_metadata,
        )

        _log.info(
            f"[TENANT_ID] Task {task.task_id} created with tenant_id='{tenant_id or 'default'}'"
        )

        return task

    async def _enqueue_file(
        orchestrator: BaseOrchestrator,
        files: list[UploadFile],
        task_type: TaskType,
        convert_options: ConvertDocumentsRequestOptions,
        chunking_options: BaseChunkerOptions | None,
        chunking_export_options: ChunkingExportOptions | None,
        target: TargetRequest,
        callbacks: list[CallbackSpec] | None = None,
        tenant_id: str | None = None,
    ) -> Task:
        _log.info(
            f"[TENANT_ID] _enqueue_file called with tenant_id='{tenant_id}', "
            f"processing {len(files)} files"
        )

        # Load the uploaded files to Docling DocumentStream
        file_sources: list[TaskSource] = []
        for i, file in enumerate(files):
            file_bytes = file.file.read()
            buf = BytesIO(file_bytes)
            suffix = "" if len(file_sources) == 1 else f"_{i}"
            name = file.filename if file.filename else f"file{suffix}.pdf"

            # Log file details for debugging transmission issues
            file_hash = hashlib.md5(file_bytes, usedforsecurity=False).hexdigest()[:12]
            _log.info(
                f"File {i}: name={name}, size={len(file_bytes)} bytes, "
                f"md5={file_hash}, content_type={file.content_type}"
            )

            file_sources.append(DocumentStream(name=name, stream=buf))

        # Prepare metadata with tenant_id BEFORE enqueueing
        metadata = {}
        if tenant_id:
            metadata["tenant_id"] = tenant_id

        task = await orchestrator.enqueue(
            task_type=task_type,
            sources=file_sources,
            convert_options=convert_options,
            chunking_options=chunking_options,
            chunking_export_options=chunking_export_options,
            targets=[target],
            callbacks=callbacks or [],
            metadata=metadata,
        )

        _log.info(
            f"[TENANT_ID] File task {task.task_id} created with tenant_id='{tenant_id or 'default'}'"
        )

        return task

    async def _enqueue_extract(
        orchestrator: BaseOrchestrator,
        request: ExtractSourcesRequest,
        tenant_id: str | None = None,
    ) -> Task:
        if docling_serve_settings.eng_kind != AsyncEngine.RAY:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "Extraction is not implemented for the configured "
                    f"'{docling_serve_settings.eng_kind.value}' engine."
                ),
            )
        validate_extract_request(request, service_policy)
        try:
            sources = [
                service_policy.source_factory.validate_config(source)
                for source in request.sources
            ]
            target = (
                service_policy.target_factory.validate_config(request.target)
                if service_policy.target_factory.supports(request.target)
                else request.target
            )
        except (SourceConnectorConfigError, TargetConnectorConfigError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc

        task_metadata: dict[str, str] = {}
        if tenant_id:
            task_metadata["tenant_id"] = tenant_id

        return await orchestrator.enqueue(
            task_type=TaskType.EXTRACT,
            sources=sources,
            extract_options=request.options,
            targets=[target],
            callbacks=request.callbacks,
            metadata=task_metadata,
        )

    def _get_tenant_id_from_header(tenant_id_header: str | None) -> str:
        """Extract tenant_id from header or return default."""
        tenant_id = tenant_id_header or "default"
        _log.info(
            f"[TENANT_ID] Extracted tenant_id from header: '{tenant_id}' "
            f"(header_value: '{tenant_id_header}')"
        )
        return tenant_id

    def _task_tenant_id(task: Task) -> str:
        """Return the tenant that owns a task, defaulting to 'default'."""
        return (task.metadata or {}).get("tenant_id") or "default"

    def _assert_task_tenant(task: Task, tenant_id: str) -> None:
        """Ensure the caller's tenant owns the task.

        Raises TaskNotFoundError (surfaced as 404) on mismatch rather than 403
        so a caller cannot probe whether a task UUID exists for another tenant.

        When tenants are not in use, every task is owned by 'default' and every
        caller resolves to 'default', so this check is transparent.
        """
        owner_tenant_id = _task_tenant_id(task)
        if owner_tenant_id != tenant_id:
            _log.warning(
                f"[TENANT_ID] Tenant mismatch for task {task.task_id}: "
                f"caller='{tenant_id}' owner='{owner_tenant_id}' - denying access"
            )
            raise TaskNotFoundError()

    async def _wait_task_complete(orchestrator: BaseOrchestrator, task_id: str) -> bool:
        start_time = time.monotonic()
        while True:
            task = await orchestrator.task_status(task_id=task_id)
            if task.is_completed():
                return True
            await asyncio.sleep(docling_serve_settings.sync_poll_interval)
            elapsed_time = time.monotonic() - start_time
            if elapsed_time > docling_serve_settings.max_sync_wait:
                return False

    def _prepare_convert_request(
        request: ConvertSourcesRequest,
    ) -> ConvertSourcesRequest:
        normalized_request = normalize_request(request, service_policy)
        validate_convert_request(normalized_request, service_policy)
        return normalized_request

    def _prepare_batch_convert_request(
        request: BatchConvertSourcesRequest,
    ) -> BatchConvertSourcesRequest:
        normalized_request = normalize_request(request, service_policy)
        validate_batch_convert_request(normalized_request, service_policy)
        return normalized_request

    def _prepare_chunk_request(
        request: GenericChunkDocumentsRequest,
    ) -> GenericChunkDocumentsRequest:
        normalized_request = request.model_copy(
            update={
                "convert_options": normalize_convert_options(
                    request.convert_options, service_policy
                )
            },
            deep=True,
        )
        validate_chunk_request(normalized_request, service_policy)
        return normalized_request

    def _prepare_convert_options(
        options: ConvertDocumentsRequestOptions,
    ) -> ConvertDocumentsRequestOptions:
        normalized_options = normalize_convert_options(options, service_policy)
        validate_convert_options(normalized_options, service_policy)
        return normalized_options

    def _validate_multipart_target_type(target_type: TargetName) -> None:
        validate_target_kind(target_type.value, service_policy)

    def _check_file_upload(files: list[UploadFile], target_type: TargetName) -> None:
        if len(files) > service_policy.max_sources_per_request:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Too many files: {len(files)} exceeds the "
                    f"maximum of {service_policy.max_sources_per_request}."
                ),
            )
        if (
            target_type == TargetName.PRESIGNED_URL
            and not service_policy.artifact_storage_enabled
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Presigned URL target requires artifact storage to be configured "
                    "and enabled on the server."
                ),
            )

    def _resolve_file_target(target_type: TargetName) -> TargetRequest:
        if target_type == TargetName.PRESIGNED_URL:
            return PresignedUrlTarget()
        if target_type == TargetName.ZIP:
            return ZipTarget()
        return InBodyTarget()

    ##########################################
    # Downgrade openapi 3.1 to 3.0.x helpers #
    ##########################################

    def ensure_array_items(schema):
        """Ensure that array items are defined."""
        if "type" in schema and schema["type"] == "array":
            if "items" not in schema or schema["items"] is None:
                schema["items"] = {"type": "string"}
            elif isinstance(schema["items"], dict):
                if "type" not in schema["items"]:
                    schema["items"]["type"] = "string"

    def handle_discriminators(schema):
        """Ensure that discriminator properties are included in required."""
        if "discriminator" in schema and "propertyName" in schema["discriminator"]:
            prop = schema["discriminator"]["propertyName"]
            if "properties" in schema and prop in schema["properties"]:
                if "required" not in schema:
                    schema["required"] = []
                if prop not in schema["required"]:
                    schema["required"].append(prop)

    def handle_properties(schema):
        """Ensure that property 'kind' is included in required."""
        if "properties" in schema and "kind" in schema["properties"]:
            if "required" not in schema:
                schema["required"] = []
            if "kind" not in schema["required"]:
                schema["required"].append("kind")

    # Downgrade openapi 3.1 to 3.0.x
    def downgrade_openapi31_to_30(spec):
        def strip_unsupported(obj):
            if isinstance(obj, dict):
                if "const" in obj and "enum" not in obj:
                    obj = {**obj, "enum": [obj["const"]]}
                obj = {
                    k: strip_unsupported(v)
                    for k, v in obj.items()
                    if k not in ("const", "examples", "prefixItems")
                }

                handle_discriminators(obj)
                ensure_array_items(obj)

                # Check for oneOf and anyOf to handle nested schemas
                for key in ["oneOf", "anyOf"]:
                    if key in obj:
                        for sub in obj[key]:
                            handle_discriminators(sub)
                            ensure_array_items(sub)

                return obj
            elif isinstance(obj, list):
                return [strip_unsupported(i) for i in obj]
            return obj

        if "components" in spec and "schemas" in spec["components"]:
            for schema_name, schema in spec["components"]["schemas"].items():
                handle_properties(schema)

        return strip_unsupported(copy.deepcopy(spec))

    #############################
    # API Endpoints definitions #
    #############################

    @app.get("/openapi-3.0.json")
    def openapi_30():
        spec = app.openapi()
        downgraded = downgrade_openapi31_to_30(spec)
        downgraded["openapi"] = "3.0.3"
        return JSONResponse(downgraded)

    # Favicon
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        logo_url = "https://raw.githubusercontent.com/docling-project/docling/refs/heads/main/docs/assets/logo.svg"
        if offline_docs_assets:
            logo_url = "/static/logo.svg"
        response = RedirectResponse(url=logo_url)
        return response

    @app.get("/health", tags=["health"])
    def health() -> HealthCheckResponse:
        _log.info("Health check requested")
        _log.debug("Processing health check")
        return HealthCheckResponse()

    @app.get("/ready", tags=["health"])
    async def readiness() -> ReadinessResponse:
        # Gate on model loading (LocalOrchestrator only; instant for RQ).
        if not _models_ready.is_set():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Models not yet loaded",
            )

        if _queue_processor_failed.is_set():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Background queue processor is not running.",
            )

        orchestrator = get_async_orchestrator()
        try:
            await orchestrator.check_connection()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=build_public_http_detail(
                    exc=exc,
                    debug_enabled=docling_serve_settings.debug_error_details,
                    fallback_message="Readiness check failed",
                ),
            ) from exc

        return ReadinessResponse()

    @app.get("/readyz", tags=["health"], include_in_schema=False)
    async def readyz() -> ReadinessResponse:
        return await readiness()

    @app.get("/livez", tags=["health"], include_in_schema=False)
    async def livez() -> HealthCheckResponse:
        # Fail liveness if the orchestrator loop has died so the platform
        # restarts the pod (which re-subscribes the pub/sub listener).
        if _queue_processor_failed.is_set():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Background queue processor is not running.",
            )
        return HealthCheckResponse()

    # API readiness compatibility for OpenShift AI Workbench
    @app.get("/api", include_in_schema=False)
    def api_check() -> HealthCheckResponse:
        return HealthCheckResponse()

    # Docling versions
    @app.get("/version", tags=["health"])
    def version_info() -> dict:
        if not docling_serve_settings.show_version_info:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden. The server is configured for not showing version details.",
            )
        return DOCLING_VERSIONS

    # Prometheus metrics endpoint
    @app.get("/metrics", tags=["health"], include_in_schema=False)
    def metrics():
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(
            content=get_metrics_endpoint_content(),
            media_type="text/plain; version=0.0.4",
        )

    # Convert a document from URL(s)
    @app.post(
        "/v1/convert/source",
        tags=["convert"],
        response_model=ConvertDocumentResponse
        | PresignedUrlConvertDocumentResponse
        | PresignedUrlConvertResponse,
        responses={
            200: {
                "content": {"application/zip": {}},
                # "description": "Return the JSON item or an image.",
            }
        },
    )
    async def process_url(
        background_tasks: BackgroundTasks,
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        conversion_request: ConvertSourcesRequestModel,
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        prepared_request = _prepare_convert_request(conversion_request)
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        _log.info(f"[TENANT_ID] process_url endpoint received tenant_id='{tenant_id}'")
        task = await _enqueue_source(
            orchestrator=orchestrator, request=prepared_request, tenant_id=tenant_id
        )
        completed = await _wait_task_complete(
            orchestrator=orchestrator, task_id=task.task_id
        )

        if not completed:
            # TODO: abort task!
            raise HTTPException(
                status_code=504,
                detail=f"Conversion is taking too long. The maximum wait time is configure as DOCLING_SERVE_MAX_SYNC_WAIT={docling_serve_settings.max_sync_wait}.",
            )

        task_result = await orchestrator.task_result(task_id=task.task_id)
        if task_result is None:
            raise HTTPException(
                status_code=404,
                detail="Task result not found. Please wait for a completion status.",
            )
        response = await prepare_response(
            task_id=task.task_id,
            task_result=task_result,
            orchestrator=orchestrator,
            background_tasks=background_tasks,
        )
        return response

    # Convert a document from file(s)
    @app.post(
        "/v1/convert/file",
        tags=["convert"],
        response_model=ConvertDocumentResponse
        | PresignedUrlConvertDocumentResponse
        | PresignedUrlConvertResponse,
        responses={
            200: {
                "content": {"application/zip": {}},
            }
        },
    )
    async def process_file(
        background_tasks: BackgroundTasks,
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        files: list[UploadFile],
        options: Annotated[
            ConvertDocumentsRequestOptions, FormDepends(ConvertDocumentsRequestOptions)
        ],
        target_type: Annotated[TargetName, Form()] = default_target_name,
        callbacks_raw: Annotated[
            list[str],
            Form(
                alias="callbacks",
                description=(
                    "Callback endpoint(s). Repeat the field for multiple callbacks. "
                    "Each value is either a bare URL (e.g. https://hook.example.com/done) "
                    'or a JSON-encoded CallbackSpec object (e.g. {"url":"https://…","headers":{"X-Token":"abc"}}).'
                ),
            ),
        ] = [],
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        _check_file_upload(files, target_type)
        options = _prepare_convert_options(options)
        _validate_multipart_target_type(target_type)
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        _log.info(f"[TENANT_ID] process_file endpoint received tenant_id='{tenant_id}'")
        target = _resolve_file_target(target_type)
        callbacks = [parse_callback_item(v) for v in callbacks_raw]
        task = await _enqueue_file(
            task_type=TaskType.CONVERT,
            orchestrator=orchestrator,
            files=files,
            convert_options=options,
            chunking_options=None,
            chunking_export_options=None,
            target=target,
            callbacks=callbacks,
            tenant_id=tenant_id,
        )
        completed = await _wait_task_complete(
            orchestrator=orchestrator, task_id=task.task_id
        )

        if not completed:
            # TODO: abort task!
            raise HTTPException(
                status_code=504,
                detail=f"Conversion is taking too long. The maximum wait time is configure as DOCLING_SERVE_MAX_SYNC_WAIT={docling_serve_settings.max_sync_wait}.",
            )

        task_result = await orchestrator.task_result(task_id=task.task_id)
        if task_result is None:
            raise HTTPException(
                status_code=404,
                detail="Task result not found. Please wait for a completion status.",
            )
        response = await prepare_response(
            task_id=task.task_id,
            task_result=task_result,
            orchestrator=orchestrator,
            background_tasks=background_tasks,
        )
        return response

    # Convert a document from URL(s) using the async api
    @app.post(
        "/v1/convert/source/async",
        tags=["convert"],
        response_model=TaskStatusResponse,
    )
    async def process_url_async(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        conversion_request: ConvertSourcesRequestModel,
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        prepared_request = _prepare_convert_request(conversion_request)
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        _log.info(
            f"[TENANT_ID] process_url_async endpoint received tenant_id='{tenant_id}'"
        )
        task = await _enqueue_source(
            orchestrator=orchestrator, request=prepared_request, tenant_id=tenant_id
        )
        task_queue_position = await orchestrator.get_queue_position(
            task_id=task.task_id
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
            error_message=task.error_message,
            failure=task.failure,
        )

    @app.post(
        "/v1/convert/source/batch",
        tags=["convert"],
        response_model=TaskStatusResponse,
    )
    async def process_source_batch(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        conversion_request: BatchConvertSourcesRequestModel,
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        conversion_request = _prepare_batch_convert_request(conversion_request)
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        _log.info(
            f"[TENANT_ID] process_source_batch endpoint received tenant_id='{tenant_id}'"
        )
        task = await _enqueue_source(
            orchestrator=orchestrator,
            request=conversion_request,
            tenant_id=tenant_id,
        )
        task_queue_position = await orchestrator.get_queue_position(
            task_id=task.task_id
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
            error_message=task.error_message,
            failure=task.failure,
        )

    # Convert a document from file(s) using the async api
    @app.post(
        "/v1/convert/file/async",
        tags=["convert"],
        response_model=TaskStatusResponse,
    )
    async def process_file_async(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        background_tasks: BackgroundTasks,
        files: list[UploadFile],
        options: Annotated[
            ConvertDocumentsRequestOptions, FormDepends(ConvertDocumentsRequestOptions)
        ],
        target_type: Annotated[TargetName, Form()] = default_target_name,
        callbacks_raw: Annotated[
            list[str],
            Form(
                alias="callbacks",
                description=(
                    "Callback endpoint(s). Repeat the field for multiple callbacks. "
                    "Each value is either a bare URL (e.g. https://hook.example.com/done) "
                    'or a JSON-encoded CallbackSpec object (e.g. {"url":"https://…","headers":{"X-Token":"abc"}}).'
                ),
            ),
        ] = [],
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        _check_file_upload(files, target_type)
        options = _prepare_convert_options(options)
        _validate_multipart_target_type(target_type)
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        _log.info(
            f"[TENANT_ID] process_file_async endpoint received tenant_id='{tenant_id}'"
        )
        target = _resolve_file_target(target_type)
        callbacks = [parse_callback_item(v) for v in callbacks_raw]
        task = await _enqueue_file(
            task_type=TaskType.CONVERT,
            orchestrator=orchestrator,
            files=files,
            convert_options=options,
            chunking_options=None,
            chunking_export_options=None,
            target=target,
            callbacks=callbacks,
            tenant_id=tenant_id,
        )
        task_queue_position = await orchestrator.get_queue_position(
            task_id=task.task_id
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
            error_message=task.error_message,
            failure=task.failure,
        )

    @app.post(
        "/v1/extract/source/async",
        tags=["extract"],
        response_model=TaskStatusResponse,
    )
    async def extract_url_async(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        extract_request: ExtractSourcesRequestModel,
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        task = await _enqueue_extract(
            orchestrator=orchestrator, request=extract_request, tenant_id=tenant_id
        )
        task_queue_position = await orchestrator.get_queue_position(
            task_id=task.task_id
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
            error_message=task.error_message,
            failure=task.failure,
        )

    # Chunking endpoints
    for display_name, path_name, opt_cls in (
        ("HybridChunker", "hybrid", HybridChunkerOptions),
        ("HierarchicalChunker", "hierarchical", HierarchicalChunkerOptions),
    ):
        req_cls = make_request_model(opt_cls)

        @app.post(
            f"/v1/chunk/{path_name}/source/async",
            name=f"Chunk sources with {display_name} as async task",
            tags=["chunk"],
            response_model=TaskStatusResponse,
        )
        async def chunk_source_async(
            background_tasks: BackgroundTasks,
            auth: Annotated[AuthenticationResult, Depends(require_auth)],
            orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
            request: req_cls,
            x_tenant_id: Annotated[
                str | None,
                Header(alias=docling_serve_settings.eng_ray_tenant_id_header),
            ] = None,
        ):
            request = _prepare_chunk_request(request)
            tenant_id = _get_tenant_id_from_header(x_tenant_id)
            _log.info(
                f"[TENANT_ID] chunk_source_async ({path_name}) endpoint received tenant_id='{tenant_id}'"
            )
            task = await _enqueue_source(
                orchestrator=orchestrator, request=request, tenant_id=tenant_id
            )
            task_queue_position = await orchestrator.get_queue_position(
                task_id=task.task_id
            )
            return TaskStatusResponse(
                task_id=task.task_id,
                task_type=task.task_type,
                task_status=task.task_status,
                task_position=task_queue_position,
                task_meta=task.processing_meta,
                error_message=task.error_message,
                failure=task.failure,
            )

        @app.post(
            f"/v1/chunk/{path_name}/file/async",
            name=f"Chunk files with {display_name} as async task",
            tags=["chunk"],
            response_model=TaskStatusResponse,
        )
        async def chunk_file_async(
            background_tasks: BackgroundTasks,
            auth: Annotated[AuthenticationResult, Depends(require_auth)],
            orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
            files: list[UploadFile],
            convert_options: Annotated[
                ConvertDocumentsRequestOptions,
                FormDepends(
                    ConvertDocumentsRequestOptions,
                    prefix="convert_",
                    excluded_fields=[
                        "to_formats",
                    ],
                ),
            ],
            chunking_options: Annotated[
                opt_cls,
                FormDepends(
                    opt_cls,
                    prefix="chunking_",
                    excluded_fields=["chunker"],
                ),
            ],
            include_converted_doc: Annotated[
                bool,
                Form(
                    description="If true, the output will include both the chunks and the converted document."
                ),
            ] = False,
            target_type: Annotated[
                TargetName,
                Form(description="Specification for the type of output target."),
            ] = TargetName.INBODY,
            x_tenant_id: Annotated[
                str | None,
                Header(alias=docling_serve_settings.eng_ray_tenant_id_header),
            ] = None,
        ):
            if target_type == TargetName.PRESIGNED_URL:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="presigned_url target is not supported for chunk endpoints.",
                )
            convert_options = _prepare_convert_options(convert_options)
            _validate_multipart_target_type(target_type)
            tenant_id = _get_tenant_id_from_header(x_tenant_id)
            _log.info(
                f"[TENANT_ID] chunk_file_async ({path_name}) endpoint received tenant_id='{tenant_id}'"
            )
            target = InBodyTarget() if target_type == TargetName.INBODY else ZipTarget()
            task = await _enqueue_file(
                task_type=TaskType.CHUNK,
                orchestrator=orchestrator,
                files=files,
                convert_options=convert_options,
                chunking_options=chunking_options,
                chunking_export_options=ChunkingExportOptions(
                    include_converted_doc=include_converted_doc
                ),
                target=target,
                callbacks=[],
                tenant_id=tenant_id,
            )
            task_queue_position = await orchestrator.get_queue_position(
                task_id=task.task_id
            )
            return TaskStatusResponse(
                task_id=task.task_id,
                task_type=task.task_type,
                task_status=task.task_status,
                task_position=task_queue_position,
                task_meta=task.processing_meta,
                error_message=task.error_message,
                failure=task.failure,
            )

        @app.post(
            f"/v1/chunk/{path_name}/source",
            name=f"Chunk sources with {display_name}",
            tags=["chunk"],
            response_model=ChunkDocumentResponse,
            responses={
                200: {
                    "content": {"application/zip": {}},
                    # "description": "Return the JSON item or an image.",
                }
            },
        )
        async def chunk_source(
            background_tasks: BackgroundTasks,
            auth: Annotated[AuthenticationResult, Depends(require_auth)],
            orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
            request: req_cls,
            x_tenant_id: Annotated[
                str | None,
                Header(alias=docling_serve_settings.eng_ray_tenant_id_header),
            ] = None,
        ):
            request = _prepare_chunk_request(request)
            tenant_id = _get_tenant_id_from_header(x_tenant_id)
            _log.info(
                f"[TENANT_ID] chunk_source ({path_name}) endpoint received tenant_id='{tenant_id}'"
            )
            task = await _enqueue_source(
                orchestrator=orchestrator, request=request, tenant_id=tenant_id
            )
            completed = await _wait_task_complete(
                orchestrator=orchestrator, task_id=task.task_id
            )

            if not completed:
                # TODO: abort task!
                raise HTTPException(
                    status_code=504,
                    detail=f"Conversion is taking too long. The maximum wait time is configure as DOCLING_SERVE_MAX_SYNC_WAIT={docling_serve_settings.max_sync_wait}.",
                )

            task_result = await orchestrator.task_result(task_id=task.task_id)
            if task_result is None:
                raise HTTPException(
                    status_code=404,
                    detail="Task result not found. Please wait for a completion status.",
                )
            response = await prepare_response(
                task_id=task.task_id,
                task_result=task_result,
                orchestrator=orchestrator,
                background_tasks=background_tasks,
            )
            return response

        @app.post(
            f"/v1/chunk/{path_name}/file",
            name=f"Chunk files with {display_name}",
            tags=["chunk"],
            response_model=ChunkDocumentResponse,
            responses={
                200: {
                    "content": {"application/zip": {}},
                }
            },
        )
        async def chunk_file(
            background_tasks: BackgroundTasks,
            auth: Annotated[AuthenticationResult, Depends(require_auth)],
            orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
            files: list[UploadFile],
            convert_options: Annotated[
                ConvertDocumentsRequestOptions,
                FormDepends(
                    ConvertDocumentsRequestOptions,
                    prefix="convert_",
                    excluded_fields=[
                        "to_formats",
                    ],
                ),
            ],
            chunking_options: Annotated[
                opt_cls,
                FormDepends(
                    opt_cls,
                    prefix="chunking_",
                    excluded_fields=["chunker"],
                ),
            ],
            include_converted_doc: Annotated[
                bool,
                Form(
                    description="If true, the output will include both the chunks and the converted document."
                ),
            ] = False,
            target_type: Annotated[
                TargetName,
                Form(description="Specification for the type of output target."),
            ] = TargetName.INBODY,
            x_tenant_id: Annotated[
                str | None,
                Header(alias=docling_serve_settings.eng_ray_tenant_id_header),
            ] = None,
        ):
            if target_type == TargetName.PRESIGNED_URL:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="presigned_url target is not supported for chunk endpoints.",
                )
            convert_options = _prepare_convert_options(convert_options)
            _validate_multipart_target_type(target_type)
            tenant_id = _get_tenant_id_from_header(x_tenant_id)
            _log.info(
                f"[TENANT_ID] chunk_file ({path_name}) endpoint received tenant_id='{tenant_id}'"
            )
            target = InBodyTarget() if target_type == TargetName.INBODY else ZipTarget()
            task = await _enqueue_file(
                task_type=TaskType.CHUNK,
                orchestrator=orchestrator,
                files=files,
                convert_options=convert_options,
                chunking_options=chunking_options,
                chunking_export_options=ChunkingExportOptions(
                    include_converted_doc=include_converted_doc
                ),
                target=target,
                callbacks=[],
                tenant_id=tenant_id,
            )
            completed = await _wait_task_complete(
                orchestrator=orchestrator, task_id=task.task_id
            )

            if not completed:
                # TODO: abort task!
                raise HTTPException(
                    status_code=504,
                    detail=f"Conversion is taking too long. The maximum wait time is configure as DOCLING_SERVE_MAX_SYNC_WAIT={docling_serve_settings.max_sync_wait}.",
                )

            task_result = await orchestrator.task_result(task_id=task.task_id)
            if task_result is None:
                raise HTTPException(
                    status_code=404,
                    detail="Task result not found. Please wait for a completion status.",
                )
            response = await prepare_response(
                task_id=task.task_id,
                task_result=task_result,
                orchestrator=orchestrator,
                background_tasks=background_tasks,
            )
            return response

    # Task status poll
    @app.get(
        "/v1/status/poll/{task_id}",
        tags=["tasks"],
        response_model=TaskStatusResponse,
    )
    async def task_status_poll(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        task_id: str,
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
        wait: Annotated[
            float,
            Query(description="Number of seconds to wait for a completed status."),
        ] = 0.0,
    ):
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        try:
            task = await orchestrator.task_status(task_id=task_id, wait=wait)
            _assert_task_tenant(task, tenant_id)
            task_queue_position = await orchestrator.get_queue_position(task_id=task_id)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        return TaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
            error_message=task.error_message,
            failure=task.failure,
        )

    # Task status websocket
    @app.websocket(
        "/v1/status/ws/{task_id}",
    )
    async def task_status_ws(
        websocket: WebSocket,
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        task_id: str,
        api_key: Annotated[str, Query()] = "",
        tenant_id: Annotated[str | None, Query()] = None,
    ):
        if docling_serve_settings.api_key:
            # WebSocket clients on this endpoint authenticate via query
            # parameter. Note that query-parameter keys may be captured in
            # proxy/access logs.
            if api_key != docling_serve_settings.api_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=(
                        "Api key is required as the ?api_key=SECRET query parameter."
                    ),
                )

        tenant_id = tenant_id or "default"

        assert isinstance(orchestrator.notifier, WebsocketNotifier)
        await websocket.accept()

        try:
            task = await orchestrator.task_status(task_id=task_id)
            _assert_task_tenant(task, tenant_id)
        except RedisBackpressureError:
            await websocket.send_text(
                WebsocketMessage(
                    message=MessageKind.ERROR,
                    error="Server is busy, please try again shortly.",
                ).model_dump_json()
            )
            await websocket.close()
            return
        except TaskNotFoundError:
            await websocket.send_text(
                WebsocketMessage(
                    message=MessageKind.ERROR, error="Task not found."
                ).model_dump_json()
            )
            await websocket.close()
            return

        # Track active WebSocket connections for this job
        orchestrator.notifier.task_subscribers.setdefault(task_id, set()).add(websocket)

        try:
            task_queue_position = await orchestrator.get_queue_position(task_id=task_id)
            task_response = TaskStatusResponse(
                task_id=task.task_id,
                task_type=task.task_type,
                task_status=task.task_status,
                task_position=task_queue_position,
                task_meta=task.processing_meta,
                error_message=task.error_message,
                failure=task.failure,
            )
            await websocket.send_text(
                WebsocketMessage(
                    message=MessageKind.CONNECTION, task=task_response
                ).model_dump_json()
            )
            while True:
                # Refresh from the orchestrator each iteration so the client
                # always sees current state — and the socket is closed on
                # completion — even if the real-time pub/sub push was missed.
                task = await orchestrator.task_status(task_id=task_id)
                task_queue_position = await orchestrator.get_queue_position(
                    task_id=task_id
                )
                task_response = TaskStatusResponse(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    task_status=task.task_status,
                    task_position=task_queue_position,
                    task_meta=task.processing_meta,
                    error_message=task.error_message,
                    failure=task.failure,
                )
                await websocket.send_text(
                    WebsocketMessage(
                        message=MessageKind.UPDATE, task=task_response
                    ).model_dump_json()
                )
                if task.is_completed():
                    await websocket.close()
                    return
                # each client message will be interpreted as a request for update
                msg = await websocket.receive_text()
                _log.debug(f"Received message: {msg}")

        except TaskNotFoundError:
            # Task was removed (e.g. reaped) while streaming; close gracefully.
            try:
                await websocket.close()
            except Exception:
                pass
        except RedisBackpressureError:
            try:
                await websocket.send_text(
                    WebsocketMessage(
                        message=MessageKind.ERROR,
                        error="Server is busy, please try again shortly.",
                    ).model_dump_json()
                )
                await websocket.close()
            except Exception:
                pass
        except WebSocketDisconnect:
            _log.info(f"WebSocket disconnected for job {task_id}")

        finally:
            subs = orchestrator.notifier.task_subscribers.get(task_id)
            if subs:
                subs.discard(websocket)

    # Task result
    @app.get(
        "/v1/result/{task_id}",
        tags=["tasks"],
        response_model=ConvertDocumentResponse
        | PresignedUrlConvertDocumentResponse
        | PresignedUrlConvertResponse
        | ChunkDocumentResponse
        | ExtractDocumentResponse
        | TaskFailureResult,
        responses={
            200: {
                "content": {"application/zip": {}},
            }
        },
    )
    async def task_result(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        background_tasks: BackgroundTasks,
        task_id: str,
        x_tenant_id: Annotated[
            str | None, Header(alias=docling_serve_settings.eng_ray_tenant_id_header)
        ] = None,
    ):
        tenant_id = _get_tenant_id_from_header(x_tenant_id)
        try:
            task = await orchestrator.task_status(task_id=task_id)
            _assert_task_tenant(task, tenant_id)
            outcome = await orchestrator.task_outcome(task_id=task_id)
            if outcome is None:
                raise HTTPException(
                    status_code=404,
                    detail="Task result not found. Please wait for a completion status.",
                )
            if isinstance(outcome, StoredFailureOutcome):
                return TaskFailureResult(failure=outcome.failure)
            if isinstance(outcome, StoredSuccessOutcome):
                task_result = outcome.result
            else:
                task_result = outcome
            response = await prepare_response(
                task_id=task_id,
                task_result=task_result,
                orchestrator=orchestrator,
                background_tasks=background_tasks,
            )
            return response
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")

    # Update task progress
    @app.post(
        "/v1/callback/task/progress",
        tags=["internal"],
        include_in_schema=False,
        response_model=ProgressCallbackResponse,
    )
    async def callback_task_progress(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        request: ProgressCallbackRequest,
    ):
        try:
            await orchestrator.receive_task_progress(request=request)
            return ProgressCallbackResponse(status="ack")
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except ProgressInvalid as err:
            raise HTTPException(
                status_code=400,
                detail=build_public_http_detail(
                    exc=err,
                    debug_enabled=docling_serve_settings.debug_error_details,
                    fallback_message="Invalid progress payload.",
                ),
            )

    #### Clear requests

    # Offload models
    @app.get(
        "/v1/clear/converters",
        tags=["clear"],
        response_model=ClearResponse,
    )
    async def clear_converters(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
    ):
        await orchestrator.clear_converters()
        return ClearResponse()

    # Clean results
    @app.get(
        "/v1/clear/results",
        tags=["clear"],
        response_model=ClearResponse,
    )
    async def clear_results(
        auth: Annotated[AuthenticationResult, Depends(require_auth)],
        orchestrator: Annotated[BaseOrchestrator, Depends(get_async_orchestrator)],
        older_then: float = 3600,
    ):
        await orchestrator.clear_results(older_than=older_then)
        return ClearResponse()

    @app.get("/v1/memory/stats", tags=["management"])
    async def memory_stats():
        if not docling_serve_settings.enable_management_endpoints:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden. The server is configured for not showing internal managament details.",
            )
        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / 1024 / 1024
        stats = {}

        # total memory (this is what triggers OOM)
        with open("/sys/fs/cgroup/memory.current") as f:  # noqa: ASYNC230
            stats["cgroup_total"] = int(f.read()) / 1024 / 1024

        # detailed breakdown
        with open("/sys/fs/cgroup/memory.stat") as f:  # noqa: ASYNC230
            for line in f:
                key, value = line.split()
                stats[key] = int(value) / 1024 / 1024

        return {
            "rss": rss_mb,
            "anon": stats.get("anon", 0.0),
            "file": stats.get("file", 0.0),
            "slab": stats.get("slab", 0.0),
            "cgroup_total": stats["cgroup_total"],
        }

    @app.get("/v1/memory/counts", tags=["management"])
    async def memory_counts():
        if not docling_serve_settings.enable_management_endpoints:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden. The server is configured for not showing internal managament details.",
            )
        gc.collect()
        objs = gc.get_objects()
        counter = Counter(type(o).__name__ for o in objs)
        tasks = asyncio.all_tasks()

        return {
            "gc": {
                "counts": gc.get_count(),
                "threshold": gc.get_threshold(),
            },
            "objects": {
                "total": len(objs),
            },
            "asyncio": {
                "all_tasks": len(tasks),
                "pending_tasks": sum(1 for t in tasks if not t.done()),
            },
            "top_types": [{"type": k, "count": v} for k, v in counter.most_common(20)],
        }

    return app
