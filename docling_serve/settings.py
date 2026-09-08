import enum
import json
import logging
import sys
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import (
    AliasChoices,
    Field,
    PositiveFloat,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from typing_extensions import Self

_log = logging.getLogger(__name__)


class UvicornSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UVICORN_", env_file=".env", extra="allow"
    )

    host: str = "0.0.0.0"
    port: int = 5001
    reload: bool = False
    root_path: str = ""
    proxy_headers: bool = True
    timeout_keep_alive: int = 60
    ssl_certfile: Optional[Path] = None
    ssl_keyfile: Optional[Path] = None
    ssl_keyfile_password: Optional[str] = None
    workers: Union[int, None] = None


class LogLevel(str, enum.Enum):
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


class LogFormat(str, enum.Enum):
    TEXT = "text"
    JSON = "json"


class AsyncEngine(str, enum.Enum):
    LOCAL = "local"
    RQ = "rq"
    RAY = "ray"


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """
    A settings source that loads configuration from a YAML or JSON file.
    The file path is specified via the DOCLING_SERVE_CONFIG_FILE environment variable.
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        # Not used in this implementation
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        """Load configuration from YAML or JSON file if config_file is set."""
        import os

        # Check for config_file in environment variable
        config_path_str = os.environ.get("DOCLING_SERVE_CONFIG_FILE")
        if not config_path_str:
            return {}

        config_path = Path(config_path_str)
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}. Fix the environment variable DOCLING_SERVE_CONFIG_FILE or unset it."
            )

        try:
            with open(config_path) as f:
                if config_path.suffix in [".yaml", ".yml"]:
                    data = yaml.safe_load(f)
                elif config_path.suffix == ".json":
                    data = json.load(f)
                else:
                    raise ValueError(
                        f"Unsupported config file format: {config_path.suffix}. Only .yaml, .yml, and .json are supported."
                    )
            if not isinstance(data, dict):
                raise ValueError(
                    f"Config file must contain a dictionary/object, got {type(data).__name__}"
                )
            return data
        except Exception as err:
            _log.error(f"Error parsing the config file {config_path}")
            raise RuntimeError(f"Failed to parse config file {config_path}") from err

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


class DoclingServeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_SERVE_",
        env_prefix_target="all",
        env_file=".env",
        env_parse_none_str="",
        extra="allow",
    )

    # Config file support
    config_file: Optional[Path] = None

    enable_ui: bool = False
    api_host: str = "localhost"
    log_level: Optional[LogLevel] = None
    log_format: LogFormat = LogFormat.TEXT
    log_header_prefix: str = "X-Docling-Log-"
    artifacts_path: Optional[Path] = None
    static_path: Optional[Path] = None
    scratch_path: Optional[Path] = None
    single_use_results: bool = True
    load_models_at_boot: bool = True
    options_cache_size: int = 2
    enable_remote_services: bool = False
    allow_external_plugins: bool = False
    allow_custom_vlm_config: bool = False
    allow_custom_picture_description_config: bool = False
    allow_custom_code_formula_config: bool = False
    allow_custom_table_structure_config: bool = False
    allow_custom_layout_config: bool = False
    allow_custom_picture_classification_config: bool = False
    allow_custom_ocr_config: bool = False
    show_version_info: bool = True
    enable_management_endpoints: bool = False
    debug_error_details: bool = False

    api_key: str = ""

    max_document_timeout: float = 3_600 * 24 * 7  # 7 days
    max_num_pages: int = sys.maxsize
    max_file_size: int = sys.maxsize
    max_sources_per_request: int = 3

    # Image export policy
    allowed_image_export_modes: Optional[list[str]] = None  # None = all modes allowed
    max_images_scale: float = 2.0

    # Artifact storage (required for PresignedUrlTarget)
    artifact_storage_enabled: bool = False
    artifact_storage_backend: Literal["s3", "azure"] = "s3"
    artifact_storage_endpoint: str = ""
    artifact_storage_verify_ssl: bool = True
    artifact_storage_bucket: str = ""
    artifact_storage_access_key: str = ""
    artifact_storage_secret_key: str = ""
    artifact_storage_key_prefix: str = "converted/"
    artifact_storage_azure_connection_string: str = ""
    artifact_storage_azure_container: str = ""
    artifact_storage_azure_account_name: str = ""
    artifact_storage_azure_blob_prefix: str = "converted/"
    artifact_storage_presign_ttl_seconds: int = 3600

    # Threading pipeline
    queue_max_size: Optional[int] = None
    ocr_batch_size: Optional[int] = None
    layout_batch_size: Optional[int] = None
    table_batch_size: Optional[int] = None
    batch_polling_interval_seconds: Optional[float] = None

    sync_poll_interval: int = 2  # seconds
    max_sync_wait: int = 120  # 2 minutes

    cors_origins: list[str] = ["*"]
    cors_methods: list[str] = ["*"]
    cors_headers: list[str] = ["*"]

    eng_kind: AsyncEngine = AsyncEngine.LOCAL
    result_removal_delay: int = 300  # seconds until result is removed after fetch
    # Local engine
    eng_loc_num_workers: int = 2
    eng_loc_share_models: bool = False
    # RQ engine
    eng_rq_redis_url: str = ""
    eng_rq_queue_name: str = "convert"
    eng_rq_results_prefix: str = "docling:results"
    eng_rq_sub_channel: str = "docling:updates"
    eng_rq_results_ttl: int = 3_600 * 4  # 4 hours default
    eng_rq_failure_ttl: int = 3_600 * 4  # 4 hours default
    eng_rq_job_timeout: int = 3_600 * 4  # 4 hours default; -1 disables it
    eng_rq_redis_max_connections: int = 50
    eng_rq_redis_socket_timeout: Optional[float] = None  # Socket timeout in seconds
    eng_rq_redis_socket_connect_timeout: Optional[float] = (
        None  # Socket connect timeout in seconds
    )
    eng_rq_redis_gate_concurrency: Optional[int] = None
    eng_rq_redis_gate_reserved_connections: int = 10
    eng_rq_redis_gate_wait_timeout: float = 0.25
    eng_rq_redis_gate_status_poll_wait_timeout: float = 5.0
    eng_rq_zombie_reaper_interval: float = 300.0
    eng_rq_zombie_reaper_max_age: float = 3600.0
    # Fair Ray engine
    # Redis Configuration
    eng_ray_redis_url: str = ""
    eng_ray_redis_max_connections: int = 50
    eng_ray_redis_socket_timeout: Optional[float] = None
    eng_ray_redis_socket_connect_timeout: Optional[float] = None
    eng_ray_redis_gate_concurrency: Optional[int] = None
    eng_ray_redis_gate_reserved_connections: int = 10
    eng_ray_redis_gate_wait_timeout: float = 0.25
    eng_ray_redis_gate_status_poll_wait_timeout: float = 5.0

    # Result Storage
    eng_ray_results_ttl: int = 3_600 * 4  # 4 hours
    eng_ray_results_prefix: str = "docling:ray:results"

    # Pub/Sub
    eng_ray_sub_channel: str = "docling:ray:updates"

    # Fair Dispatcher
    eng_ray_dispatcher_interval: float = 30.0
    eng_ray_supervisor_poll_interval: float = 5.0

    # Per-User Dispatcher Limits
    eng_ray_max_concurrent_tasks: int = 5
    eng_ray_max_queued_tasks: Optional[int] = None
    eng_ray_enable_queue_limit_rejection: bool = False
    eng_ray_max_documents: Optional[int] = None
    eng_ray_enable_document_limits: bool = False

    # Ray Configuration
    eng_ray_address: str = ""  # Required - must be set explicitly
    eng_ray_namespace: str = "docling"
    eng_ray_runtime_env: Optional[dict] = None

    # Ray mTLS Configuration
    eng_ray_enable_mtls: bool = False
    eng_ray_cluster_name: Optional[str] = None

    # Ray Serve Autoscaling
    eng_ray_min_actors: int = 1
    eng_ray_max_actors: int = 10
    eng_ray_target_requests_per_replica: PositiveFloat = 1.0
    # Hard cap on concurrent in-flight requests per replica.
    # None -> follow eng_ray_target_requests_per_replica.
    eng_ray_max_ongoing_requests_per_replica: Optional[int] = None
    # Hard cap on converter Serve replicas per Ray node. None -> no cap.
    eng_ray_converter_max_replicas_per_node: Optional[int] = None
    eng_ray_upscale_delay_s: float = 30.0
    eng_ray_downscale_delay_s: float = 600.0
    # None -> use Ray Serve defaults.
    eng_ray_graceful_shutdown_wait_loop_s: Optional[float] = None
    eng_ray_graceful_shutdown_timeout_s: Optional[float] = None
    eng_ray_converter_actor_num_cpus: float = Field(
        1.0,
        validation_alias=AliasChoices(
            "eng_ray_converter_actor_num_cpus",
            "eng_ray_num_cpus_per_actor",
        ),
    )
    eng_ray_enable_pdf_page_slice_fanout: bool = False
    eng_ray_max_page_slice_size: int = 32
    # Unset means "default to eng_ray_max_concurrent_tasks" at runtime.
    # Explicit values override that default but fan-out should never be unbounded.
    eng_ray_max_page_slice_parallelism: Optional[int] = None
    eng_ray_coordinator_min_actors: Optional[int] = None
    eng_ray_coordinator_max_actors: Optional[int] = None
    eng_ray_coordinator_target_requests_per_replica: Optional[PositiveFloat] = None
    eng_ray_coordinator_max_ongoing_requests_per_replica: int = 8
    # Hard cap on coordinator Serve replicas per Ray node. None -> no cap.
    eng_ray_coordinator_max_replicas_per_node: Optional[int] = None
    eng_ray_coordinator_actor_num_cpus: float = 0.25
    eng_ray_coordinator_actor_memory_request: Optional[str] = None

    # Fault Tolerance & Retry
    eng_ray_max_task_retries: int = 3
    eng_ray_retry_delay: float = 5.0
    eng_ray_max_document_retries: int = 2

    # Ray Actor Configuration
    eng_ray_dispatcher_max_restarts: int = -1
    eng_ray_dispatcher_max_task_retries: int = 3

    # Timeouts
    eng_ray_task_timeout: Optional[float] = 3600.0
    eng_ray_document_timeout: Optional[float] = 300.0
    eng_ray_redis_operation_timeout: float = 30.0
    eng_ray_dispatcher_rpc_timeout: float = 5.0
    eng_ray_liveness_fail_after: float = 90.0

    # Health Checks
    eng_ray_enable_heartbeat: bool = True

    # Resource Management & Memory Monitoring
    eng_ray_converter_actor_memory_request: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "eng_ray_converter_actor_memory_request",
            "eng_ray_memory_limit_per_actor",
        ),
    )
    eng_ray_dispatcher_num_cpus: float = 0.25
    eng_ray_dispatcher_memory_request: Optional[str] = None
    eng_ray_object_store_memory: Optional[str] = None
    eng_ray_enable_oom_protection: bool = True
    eng_ray_memory_warning_threshold: float = 0.9

    # Scratch Directory
    eng_ray_scratch_dir: Optional[Path] = None

    # Logging
    eng_ray_log_level: str = "INFO"

    # Metrics
    eng_ray_generate_metrics: bool = False
    eng_ray_metrics_port: int = 8090

    # Tenant ID Header
    eng_ray_tenant_id_header: str = "X-Tenant-Id"

    # OpenTelemetry settings
    otel_enable_metrics: bool = True
    otel_enable_traces: bool = False
    otel_enable_prometheus: bool = True
    otel_enable_otlp_metrics: bool = False
    otel_service_name: str = "docling-serve"

    # Metrics
    metrics_port: Optional[int] = None

    # === DoclingConverterManagerConfig Parameters ===
    # TODO: Don't overwrite the default of docling-jobkit. This requires first some restructure in jobkit.

    # VLM Pipeline Control
    default_vlm_preset: str = "granite_docling"
    allowed_vlm_presets: Optional[list[str]] = None
    custom_vlm_presets: dict[str, Any] = Field(default_factory=dict)
    allowed_vlm_engines: Optional[list[str]] = None

    # Picture Description Control
    default_picture_description_preset: str = "smolvlm"
    allowed_picture_description_presets: Optional[list[str]] = None
    custom_picture_description_presets: dict[str, Any] = Field(default_factory=dict)
    allowed_picture_description_engines: Optional[list[str]] = None

    # Code/Formula Control
    default_code_formula_preset: str = "default"
    allowed_code_formula_presets: Optional[list[str]] = None
    custom_code_formula_presets: dict[str, Any] = Field(default_factory=dict)
    allowed_code_formula_engines: Optional[list[str]] = None

    # Picture Classification Control
    default_picture_classification_preset: str = "document_figure_classifier_v2"
    allowed_picture_classification_presets: Optional[list[str]] = None
    custom_picture_classification_presets: dict[str, Any] = Field(default_factory=dict)

    # Table Structure Control
    default_table_structure_kind: str = "docling_tableformer"
    allowed_table_structure_kinds: Optional[list[str]] = None
    default_table_structure_preset: str = "tableformer_v1_accurate"
    allowed_table_structure_presets: Optional[list[str]] = None
    custom_table_structure_presets: dict[str, Any] = Field(default_factory=dict)

    # Layout Control
    default_layout_kind: str = "docling_layout_default"
    allowed_layout_kinds: Optional[list[str]] = None
    default_layout_preset: str = "docling_layout_default"
    allowed_layout_presets: Optional[list[str]] = None
    custom_layout_presets: dict[str, Any] = Field(default_factory=dict)

    # OCR Control
    default_ocr_preset: str = "auto"
    default_ocr_kind: str = "auto"
    allowed_ocr_presets: Optional[list[str]] = None
    custom_ocr_presets: dict[str, Any] = Field(default_factory=dict)
    allowed_ocr_kinds: Optional[list[str]] = None

    # Chunking Control
    default_chunking_preset: str = "granite_embedding_278m"
    allowed_chunking_presets: Optional[list[str]] = None
    custom_chunking_presets: dict[str, Any] = Field(default_factory=dict)

    # Source / Target Control
    allowed_source_types: Optional[list[str]] = None
    allowed_target_types: Optional[list[str]] = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Customize settings sources to include YAML/JSON config file support.
        Priority order: init > env > dotenv > yaml_config > file_secret
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator(
        "custom_vlm_presets",
        "custom_picture_description_presets",
        "custom_code_formula_presets",
        "custom_picture_classification_presets",
        "custom_table_structure_presets",
        "custom_layout_presets",
        "custom_ocr_presets",
        "custom_chunking_presets",
        mode="before",
    )
    @classmethod
    def parse_dict_from_json(cls, v: Any) -> dict[str, Any]:
        """Parse dict parameters from JSON-serialized ENV variables."""
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return parsed
                return {}
            except json.JSONDecodeError:
                return {}
        return {}

    @field_validator(
        "allowed_vlm_presets",
        "allowed_vlm_engines",
        "allowed_picture_description_presets",
        "allowed_picture_description_engines",
        "allowed_code_formula_presets",
        "allowed_code_formula_engines",
        "allowed_picture_classification_presets",
        "allowed_table_structure_kinds",
        "allowed_table_structure_presets",
        "allowed_layout_kinds",
        "allowed_layout_presets",
        "allowed_ocr_presets",
        "allowed_ocr_kinds",
        "allowed_chunking_presets",
        "allowed_source_types",
        "allowed_target_types",
        "allowed_image_export_modes",
        mode="before",
    )
    @classmethod
    def parse_list_from_json_or_csv(cls, v: Any) -> Optional[list[str]]:
        """Parse list parameters from JSON arrays or comma-separated strings."""
        if v is None or v == "":
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            # Try JSON first
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                pass
            # Fall back to comma-separated
            items = [item.strip() for item in v.split(",") if item.strip()]
            return items if items else None
        return None

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: Optional[str]) -> Optional[str]:
        """Validate and normalize log level to uppercase for case-insensitive support."""
        if v is None:
            return v
        if isinstance(v, str):
            return v.upper()
        return v

    @model_validator(mode="before")
    @classmethod
    def warn_deprecated_ray_settings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            deprecated_keys = {
                "eng_ray_num_cpus_per_actor": "eng_ray_converter_actor_num_cpus",
                "eng_ray_memory_limit_per_actor": "eng_ray_converter_actor_memory_request",
            }
            for old_key, new_key in deprecated_keys.items():
                if old_key in data:
                    _log.warning("%s is deprecated; use %s instead.", old_key, new_key)

        return data

    @model_validator(mode="after")
    def engine_settings(self) -> Self:
        if self.eng_kind == AsyncEngine.RQ:
            if not self.eng_rq_redis_url:
                raise ValueError("RQ Redis url is required when using the RQ engine.")

        if self.eng_kind == AsyncEngine.RAY:
            if not self.eng_ray_redis_url:
                raise ValueError(
                    "Fair Ray Redis URL is required when using the RAY engine."
                )
            if not self.eng_ray_address:
                raise ValueError(
                    "Fair Ray address is required when using the RAY engine. "
                    "Use 'auto' or 'local' for local Ray, or provide a Ray cluster address."
                )

        return self


uvicorn_settings = UvicornSettings()
docling_serve_settings = DoclingServeSettings()
