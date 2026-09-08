import logging
from functools import lru_cache

from docling_jobkit.orchestrators.base_orchestrator import BaseOrchestrator

from docling_serve.settings import AsyncEngine, docling_serve_settings
from docling_serve.storage import get_scratch

_log = logging.getLogger(__name__)


def _build_cm_config():
    from docling_jobkit.convert.manager import DoclingConverterManagerConfig

    return DoclingConverterManagerConfig(
        artifacts_path=docling_serve_settings.artifacts_path,
        options_cache_size=docling_serve_settings.options_cache_size,
        enable_remote_services=docling_serve_settings.enable_remote_services,
        allow_external_plugins=docling_serve_settings.allow_external_plugins,
        max_num_pages=docling_serve_settings.max_num_pages,
        max_file_size=docling_serve_settings.max_file_size,
        queue_max_size=docling_serve_settings.queue_max_size,
        ocr_batch_size=docling_serve_settings.ocr_batch_size,
        layout_batch_size=docling_serve_settings.layout_batch_size,
        table_batch_size=docling_serve_settings.table_batch_size,
        batch_polling_interval_seconds=docling_serve_settings.batch_polling_interval_seconds,
        # VLM Pipeline Control
        default_vlm_preset=docling_serve_settings.default_vlm_preset,
        allowed_vlm_presets=docling_serve_settings.allowed_vlm_presets,
        custom_vlm_presets=docling_serve_settings.custom_vlm_presets,
        allowed_vlm_engines=docling_serve_settings.allowed_vlm_engines,
        allow_custom_vlm_config=docling_serve_settings.allow_custom_vlm_config,
        # Picture Description Control
        default_picture_description_preset=docling_serve_settings.default_picture_description_preset,
        allowed_picture_description_presets=docling_serve_settings.allowed_picture_description_presets,
        custom_picture_description_presets=docling_serve_settings.custom_picture_description_presets,
        allowed_picture_description_engines=docling_serve_settings.allowed_picture_description_engines,
        allow_custom_picture_description_config=docling_serve_settings.allow_custom_picture_description_config,
        # Code/Formula Control
        default_code_formula_preset=docling_serve_settings.default_code_formula_preset,
        allowed_code_formula_presets=docling_serve_settings.allowed_code_formula_presets,
        custom_code_formula_presets=docling_serve_settings.custom_code_formula_presets,
        allowed_code_formula_engines=docling_serve_settings.allowed_code_formula_engines,
        allow_custom_code_formula_config=docling_serve_settings.allow_custom_code_formula_config,
        # Picture Classification Control
        default_picture_classification_preset=docling_serve_settings.default_picture_classification_preset,
        allowed_picture_classification_presets=docling_serve_settings.allowed_picture_classification_presets,
        custom_picture_classification_presets=docling_serve_settings.custom_picture_classification_presets,
        allow_custom_picture_classification_config=docling_serve_settings.allow_custom_picture_classification_config,
        # Table Structure Control
        default_table_structure_kind=docling_serve_settings.default_table_structure_kind,
        allowed_table_structure_kinds=docling_serve_settings.allowed_table_structure_kinds,
        default_table_structure_preset=docling_serve_settings.default_table_structure_preset,
        allowed_table_structure_presets=docling_serve_settings.allowed_table_structure_presets,
        custom_table_structure_presets=docling_serve_settings.custom_table_structure_presets,
        allow_custom_table_structure_config=docling_serve_settings.allow_custom_table_structure_config,
        # Layout Control
        default_layout_kind=docling_serve_settings.default_layout_kind,
        allowed_layout_kinds=docling_serve_settings.allowed_layout_kinds,
        default_layout_preset=docling_serve_settings.default_layout_preset,
        allowed_layout_presets=docling_serve_settings.allowed_layout_presets,
        custom_layout_presets=docling_serve_settings.custom_layout_presets,
        allow_custom_layout_config=docling_serve_settings.allow_custom_layout_config,
        # OCR Control
        default_ocr_preset=docling_serve_settings.default_ocr_preset,
        default_ocr_kind=docling_serve_settings.default_ocr_kind,
        allowed_ocr_presets=docling_serve_settings.allowed_ocr_presets,
        custom_ocr_presets=docling_serve_settings.custom_ocr_presets,
        allowed_ocr_kinds=docling_serve_settings.allowed_ocr_kinds,
        allow_custom_ocr_config=docling_serve_settings.allow_custom_ocr_config,
        # Chunking Control
        default_chunking_preset=docling_serve_settings.default_chunking_preset,
        allowed_chunking_presets=docling_serve_settings.allowed_chunking_presets,
        custom_chunking_presets=docling_serve_settings.custom_chunking_presets,
    )


def _build_presigned_config():
    """Build presigned artifact storage config, or return None when disabled.

    The managed storage prefix and URL TTL are owned by docling-serve settings and
    injected into jobkit here before any orchestrator is constructed.
    """
    if not docling_serve_settings.artifact_storage_enabled:
        return None

    if docling_serve_settings.artifact_storage_backend == "azure":
        from docling.datamodel.service.sources import AzureBlobCoordinates
        from docling_jobkit.config.target_config import AzurePresignedConfig

        required_settings = {
            "DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_CONNECTION_STRING": (
                docling_serve_settings.artifact_storage_azure_connection_string
            ),
            "DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_CONTAINER": (
                docling_serve_settings.artifact_storage_azure_container
            ),
            "DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_ACCOUNT_NAME": (
                docling_serve_settings.artifact_storage_azure_account_name
            ),
        }
        missing = [name for name, value in required_settings.items() if not value]
        if missing:
            raise ValueError(
                "Azure managed artifact storage requires: " + ", ".join(missing)
            )

        return AzurePresignedConfig(
            azure_coords=AzureBlobCoordinates(
                account_name=docling_serve_settings.artifact_storage_azure_account_name,
                container=docling_serve_settings.artifact_storage_azure_container,
                connection_string=(
                    docling_serve_settings.artifact_storage_azure_connection_string
                ),
                blob_prefix=docling_serve_settings.artifact_storage_azure_blob_prefix,
            ),
            url_expiration=(
                docling_serve_settings.artifact_storage_presign_ttl_seconds
            ),
        )

    from docling.datamodel.service.sources import S3Coordinates
    from docling_jobkit.config.target_config import S3PresignedConfig

    coords = S3Coordinates(
        endpoint=docling_serve_settings.artifact_storage_endpoint,
        verify_ssl=docling_serve_settings.artifact_storage_verify_ssl,
        access_key=docling_serve_settings.artifact_storage_access_key,
        secret_key=docling_serve_settings.artifact_storage_secret_key,
        bucket=docling_serve_settings.artifact_storage_bucket,
        key_prefix=docling_serve_settings.artifact_storage_key_prefix,
    )
    return S3PresignedConfig(
        s3_coords=coords,
        url_expiration=docling_serve_settings.artifact_storage_presign_ttl_seconds,
    )


def _build_rq_config():
    from docling_jobkit.orchestrators.rq.orchestrator import RQOrchestratorConfig

    return RQOrchestratorConfig(
        redis_url=docling_serve_settings.eng_rq_redis_url,
        queue_name=docling_serve_settings.eng_rq_queue_name,
        results_prefix=docling_serve_settings.eng_rq_results_prefix,
        sub_channel=docling_serve_settings.eng_rq_sub_channel,
        scratch_dir=get_scratch(),
        debug_error_details=docling_serve_settings.debug_error_details,
        results_ttl=docling_serve_settings.eng_rq_results_ttl,
        failure_ttl=docling_serve_settings.eng_rq_failure_ttl,
        job_timeout=docling_serve_settings.eng_rq_job_timeout,
        redis_max_connections=docling_serve_settings.eng_rq_redis_max_connections,
        redis_socket_timeout=docling_serve_settings.eng_rq_redis_socket_timeout,
        redis_socket_connect_timeout=(
            docling_serve_settings.eng_rq_redis_socket_connect_timeout
        ),
        redis_gate_concurrency=docling_serve_settings.eng_rq_redis_gate_concurrency,
        redis_gate_reserved_connections=(
            docling_serve_settings.eng_rq_redis_gate_reserved_connections
        ),
        redis_gate_wait_timeout=docling_serve_settings.eng_rq_redis_gate_wait_timeout,
        redis_gate_status_poll_wait_timeout=(
            docling_serve_settings.eng_rq_redis_gate_status_poll_wait_timeout
        ),
        zombie_reaper_interval=docling_serve_settings.eng_rq_zombie_reaper_interval,
        zombie_reaper_max_age=docling_serve_settings.eng_rq_zombie_reaper_max_age,
        result_removal_delay=docling_serve_settings.result_removal_delay,
        presigned_config=_build_presigned_config(),
        allow_external_plugins=docling_serve_settings.allow_external_plugins,
    )


@lru_cache
def get_async_orchestrator() -> BaseOrchestrator:
    if docling_serve_settings.eng_kind == AsyncEngine.LOCAL:
        from docling_jobkit.convert.manager import DoclingConverterManager
        from docling_jobkit.orchestrators.local.orchestrator import (
            LocalOrchestrator,
            LocalOrchestratorConfig,
        )

        local_config = LocalOrchestratorConfig(
            num_workers=docling_serve_settings.eng_loc_num_workers,
            shared_models=docling_serve_settings.eng_loc_share_models,
            scratch_dir=get_scratch(),
            result_removal_delay=docling_serve_settings.result_removal_delay,
            presigned_config=_build_presigned_config(),
        )

        cm = DoclingConverterManager(config=_build_cm_config())

        return LocalOrchestrator(config=local_config, converter_manager=cm)

    elif docling_serve_settings.eng_kind == AsyncEngine.RQ:
        from docling_jobkit.orchestrators.rq.orchestrator import RQOrchestrator

        from docling_serve.rq_instrumentation import wrap_rq_queue_for_tracing

        orchestrator = RQOrchestrator(config=_build_rq_config())
        if docling_serve_settings.otel_enable_traces:
            wrap_rq_queue_for_tracing(orchestrator._rq_queue)
            orchestrator._rq_job_function = (
                "docling_serve.rq_job_wrapper.instrumented_docling_task"
            )
        return orchestrator

    elif docling_serve_settings.eng_kind == AsyncEngine.RAY:
        from docling_jobkit.convert.manager import DoclingConverterManager
        from docling_jobkit.orchestrators.ray.config import (
            RayOrchestratorConfig,
        )
        from docling_jobkit.orchestrators.ray.orchestrator import (
            RayOrchestrator,
        )

        max_page_slice_parallelism = (
            docling_serve_settings.eng_ray_max_page_slice_parallelism
            if docling_serve_settings.eng_ray_max_page_slice_parallelism is not None
            else docling_serve_settings.eng_ray_max_concurrent_tasks
        )

        cm = DoclingConverterManager(config=_build_cm_config())

        # Create Fair Ray orchestrator config
        ray_config = RayOrchestratorConfig(
            presigned_config=_build_presigned_config(),
            # Redis Configuration
            redis_url=docling_serve_settings.eng_ray_redis_url,
            redis_max_connections=docling_serve_settings.eng_ray_redis_max_connections,
            redis_socket_timeout=docling_serve_settings.eng_ray_redis_socket_timeout,
            redis_socket_connect_timeout=docling_serve_settings.eng_ray_redis_socket_connect_timeout,
            redis_gate_concurrency=docling_serve_settings.eng_ray_redis_gate_concurrency,
            redis_gate_reserved_connections=docling_serve_settings.eng_ray_redis_gate_reserved_connections,
            redis_gate_wait_timeout=docling_serve_settings.eng_ray_redis_gate_wait_timeout,
            redis_gate_status_poll_wait_timeout=docling_serve_settings.eng_ray_redis_gate_status_poll_wait_timeout,
            # Result Storage
            results_ttl=docling_serve_settings.eng_ray_results_ttl,
            results_prefix=docling_serve_settings.eng_ray_results_prefix,
            result_removal_delay=docling_serve_settings.result_removal_delay,
            # Pub/Sub
            sub_channel=docling_serve_settings.eng_ray_sub_channel,
            # Fair Dispatcher
            dispatcher_interval=docling_serve_settings.eng_ray_dispatcher_interval,
            supervisor_poll_interval=docling_serve_settings.eng_ray_supervisor_poll_interval,
            # Per-User Limits
            max_concurrent_tasks=docling_serve_settings.eng_ray_max_concurrent_tasks,
            max_queued_tasks=docling_serve_settings.eng_ray_max_queued_tasks,
            enable_queue_limit_rejection=docling_serve_settings.eng_ray_enable_queue_limit_rejection,
            max_documents=docling_serve_settings.eng_ray_max_documents,
            enable_document_limits=docling_serve_settings.eng_ray_enable_document_limits,
            # Ray Configuration
            ray_address=(
                None
                if docling_serve_settings.eng_ray_address in ["auto", "local"]
                else docling_serve_settings.eng_ray_address
            ),
            ray_namespace=docling_serve_settings.eng_ray_namespace,
            ray_runtime_env=docling_serve_settings.eng_ray_runtime_env,
            # Ray mTLS Configuration
            enable_mtls=docling_serve_settings.eng_ray_enable_mtls,
            ray_cluster_name=docling_serve_settings.eng_ray_cluster_name,
            # Ray Serve Autoscaling
            min_actors=docling_serve_settings.eng_ray_min_actors,
            max_actors=docling_serve_settings.eng_ray_max_actors,
            target_requests_per_replica=docling_serve_settings.eng_ray_target_requests_per_replica,
            max_ongoing_requests_per_replica=docling_serve_settings.eng_ray_max_ongoing_requests_per_replica,
            converter_max_replicas_per_node=docling_serve_settings.eng_ray_converter_max_replicas_per_node,
            upscale_delay_s=docling_serve_settings.eng_ray_upscale_delay_s,
            downscale_delay_s=docling_serve_settings.eng_ray_downscale_delay_s,
            graceful_shutdown_wait_loop_s=docling_serve_settings.eng_ray_graceful_shutdown_wait_loop_s,
            graceful_shutdown_timeout_s=docling_serve_settings.eng_ray_graceful_shutdown_timeout_s,
            converter_actor_num_cpus=docling_serve_settings.eng_ray_converter_actor_num_cpus,
            enable_pdf_page_slice_fanout=docling_serve_settings.eng_ray_enable_pdf_page_slice_fanout,
            max_page_slice_size=docling_serve_settings.eng_ray_max_page_slice_size,
            max_page_slice_parallelism=max_page_slice_parallelism,
            coordinator_min_actors=docling_serve_settings.eng_ray_coordinator_min_actors,
            coordinator_max_actors=docling_serve_settings.eng_ray_coordinator_max_actors,
            coordinator_target_requests_per_replica=docling_serve_settings.eng_ray_coordinator_target_requests_per_replica,
            coordinator_max_ongoing_requests_per_replica=docling_serve_settings.eng_ray_coordinator_max_ongoing_requests_per_replica,
            coordinator_max_replicas_per_node=docling_serve_settings.eng_ray_coordinator_max_replicas_per_node,
            coordinator_actor_num_cpus=docling_serve_settings.eng_ray_coordinator_actor_num_cpus,
            coordinator_actor_memory_request=docling_serve_settings.eng_ray_coordinator_actor_memory_request,
            # Fault Tolerance & Retry
            max_task_retries=docling_serve_settings.eng_ray_max_task_retries,
            retry_delay=docling_serve_settings.eng_ray_retry_delay,
            max_document_retries=docling_serve_settings.eng_ray_max_document_retries,
            # Ray Actor Configuration
            dispatcher_max_restarts=docling_serve_settings.eng_ray_dispatcher_max_restarts,
            dispatcher_max_task_retries=docling_serve_settings.eng_ray_dispatcher_max_task_retries,
            # Timeouts
            task_timeout=docling_serve_settings.eng_ray_task_timeout,
            document_timeout=docling_serve_settings.eng_ray_document_timeout,
            redis_operation_timeout=docling_serve_settings.eng_ray_redis_operation_timeout,
            dispatcher_rpc_timeout=docling_serve_settings.eng_ray_dispatcher_rpc_timeout,
            liveness_fail_after=docling_serve_settings.eng_ray_liveness_fail_after,
            # Health Checks
            enable_heartbeat=docling_serve_settings.eng_ray_enable_heartbeat,
            # Resource Management & Memory Monitoring
            converter_actor_memory_request=docling_serve_settings.eng_ray_converter_actor_memory_request,
            dispatcher_num_cpus=docling_serve_settings.eng_ray_dispatcher_num_cpus,
            dispatcher_memory_request=docling_serve_settings.eng_ray_dispatcher_memory_request,
            ray_object_store_memory=docling_serve_settings.eng_ray_object_store_memory,
            enable_oom_protection=docling_serve_settings.eng_ray_enable_oom_protection,
            memory_warning_threshold=docling_serve_settings.eng_ray_memory_warning_threshold,
            # Scratch Directory
            scratch_dir=docling_serve_settings.eng_ray_scratch_dir or get_scratch(),
            # Logging
            log_level=docling_serve_settings.eng_ray_log_level,
            debug_error_details=docling_serve_settings.debug_error_details,
            # Metrics
            generate_metrics=docling_serve_settings.eng_ray_generate_metrics,
            metrics_port=docling_serve_settings.eng_ray_metrics_port,
        )

        return RayOrchestrator(config=ray_config, converter_manager=cm)

    raise RuntimeError(f"Engine {docling_serve_settings.eng_kind} not recognized.")
