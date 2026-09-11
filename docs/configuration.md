# Configuration

The `docling-serve` executable allows to configure the server via command line
options as well as environment variables.
Configurations are divided between the settings used for the `uvicorn` asgi
server and the actual app-specific configurations.

 > [!WARNING]
> When the server is running with `reload` or with multiple `workers`, uvicorn
> will spawn multiple subprocesses. This invalidates all the values configured
> via the CLI command line options. Please use environment variables in this
> type of deployments.

## Webserver configuration

The following table shows the options which are propagated directly to the
`uvicorn` webserver runtime.

| CLI option | ENV | Default | Description |
| -----------|-----|---------|-------------|
| `--host` | `UVICORN_HOST` | `0.0.0.0` for `run`, `localhost` for `dev` | THe host to serve on. |
| `--port` | `UVICORN_PORT` | `5001` | The port to serve on. |
| `--reload` | `UVICORN_RELOAD` | `false` for `run`, `true` for `dev` | Enable auto-reload of the server when (code) files change. |
| `--workers` | `UVICORN_WORKERS` | `1` | Use multiple worker processes. |
| `--root-path` | `UVICORN_ROOT_PATH` | `""` | The root path is used to tell your app that it is being served to the outside world with some |
| `--proxy-headers` | `UVICORN_PROXY_HEADERS` | `true` | Enable/Disable X-Forwarded-Proto, X-Forwarded-For, X-Forwarded-Port to populate remote address info. |
| `--timeout-keep-alive` | `UVICORN_TIMEOUT_KEEP_ALIVE` | `60` | Timeout for the server response. |
| `--ssl-certfile` | `UVICORN_SSL_CERTFILE` |  | SSL certificate file. |
| `--ssl-keyfile` | `UVICORN_SSL_KEYFILE` |  | SSL key file. |
| `--ssl-keyfile-password` | `UVICORN_SSL_KEYFILE_PASSWORD` |  | SSL keyfile password. |

## Docling Serve configuration

THe following table describes the options to configure the Docling Serve app.

| CLI option | ENV | Default | Description |
| -----------|-----|---------|-------------|
| `-v, --verbose` | `DOCLING_SERVE_LOG_LEVEL` | `WARNING` | Set the verbosity level. CLI: `-v` for INFO, `-vv` for DEBUG. ENV: `WARNING`, `INFO`, or `DEBUG` (case-insensitive). CLI flag takes precedence over ENV. |
|  | `DOCLING_SERVE_LOG_FORMAT` | `text` | Log output format. Options: `text` (colored console logs) or `json` (structured JSON logs). JSON format is recommended for production deployments and log aggregation systems. |
|  | `DOCLING_SERVE_LOG_HEADER_PREFIX` | `X-Docling-Log-` | Prefix for HTTP request headers that should be propagated to logs. Headers matching this prefix (case-insensitive) will be extracted and included as structured fields in all logs during the request lifecycle. Example: `X-Docling-Log-RequestID` becomes `RequestID` in logs. |
| `--artifacts-path` | `DOCLING_SERVE_ARTIFACTS_PATH` | unset | If set to a valid directory, the model weights will be loaded from this path |
|  | `DOCLING_SERVE_STATIC_PATH` | unset | If set to a valid directory, the static assets for the docs and UI will be loaded from this path |
|  | `DOCLING_SERVE_SCRATCH_PATH` |  | If set, this directory will be used as scratch workspace, e.g. storing the results before they get requested. If unset, a temporary created is created for this purpose. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_ENABLED` | `false` | Enable server-managed artifact storage for `PresignedUrlTarget`. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_BACKEND` | `s3` | Managed artifact-storage backend: `s3` or `azure`. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_ENDPOINT` |  | S3 endpoint without protocol. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_REGION` | unset | AWS region of the S3 bucket, e.g. `us-east-2`. Required for correctly signed requests (including presigned URLs) against real AWS S3 buckets outside `us-east-1`. Not required for S3-compatible services (e.g. IBM COS, MinIO) that ignore region. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_VERIFY_SSL` | `true` | Whether the S3 artifact-storage connection verifies TLS certificates. Set this to `false` for local HTTP or self-signed MinIO setups. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_BUCKET` |  | S3 bucket for managed artifacts. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_ACCESS_KEY` |  | S3 access key. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_SECRET_KEY` |  | S3 secret key. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_KEY_PREFIX` | `converted/` | S3 object-key prefix for managed artifacts. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_CONNECTION_STRING` |  | Azure Storage connection string. It must contain `AccountName` and `AccountKey`; managed identity, SAS-only connection strings, and `UseDevelopmentStorage=true` are not supported for managed artifacts. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_CONTAINER` |  | Azure Blob container for managed artifacts. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_ACCOUNT_NAME` |  | Azure Storage account name. It must match `AccountName` in the connection string. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_AZURE_BLOB_PREFIX` | `converted/` | Azure blob-name prefix for managed artifacts. |
|  | `DOCLING_SERVE_ARTIFACT_STORAGE_PRESIGN_TTL_SECONDS` | `3600` | Lifetime of returned S3 presigned URLs or Azure Blob SAS URLs. Valid range: 60–604800 seconds. |
| `--enable-ui` | `DOCLING_SERVE_ENABLE_UI` | `false` | Enable the demonstrator UI. |
|  | `DOCLING_SERVE_ENABLE_MANAGEMENT_ENDPOINTS` | `false` | If enabled, the `/v1/memory` endpoints will provide memory statistics, otherwise it will return a forbidden 403 error. |
|  | `DOCLING_SERVE_SHOW_VERSION_INFO` | `true` | If enabled, the `/version` endpoint will provide the Docling package versions, otherwise it will return a forbidden 403 error. |
|  | `DOCLING_SERVE_DEBUG_ERROR_DETAILS` | `false` | If enabled, raw internal exception detail is returned for debugging. When `false`, infrastructure-origin error details are sanitized in public HTTP/task surfaces. |
|  | `DOCLING_SERVE_ENABLE_REMOTE_SERVICES` | `false` | Allow pipeline components making remote connections. For example, this is needed when using a vision-language model via APIs. |
|  | `DOCLING_SERVE_ALLOW_EXTERNAL_PLUGINS` | `false` | Allow registered third-party plugins. Connector packages must be installed in every API and worker process. |
|  | `DOCLING_SERVE_ALLOW_CUSTOM_VLM_CONFIG` | `false` | Allow users to specify a fully custom VLM pipeline configuration (`vlm_pipeline_custom_config`). When `false`, only presets are accepted. |
|  | `DOCLING_SERVE_ALLOW_CUSTOM_PICTURE_DESCRIPTION_CONFIG` | `false` | Allow users to specify a fully custom picture description configuration. When `false`, only presets are accepted. |
|  | `DOCLING_SERVE_ALLOW_CUSTOM_CODE_FORMULA_CONFIG` | `false` | Allow users to specify a fully custom code/formula configuration. When `false`, only presets are accepted. |
|  | `DOCLING_SERVE_SINGLE_USE_RESULTS` | `true` | If true, results can be accessed only once. If false, the results accumulate in the scratch directory. |
|  | `DOCLING_SERVE_RESULT_REMOVAL_DELAY` | `300` | When `DOCLING_SERVE_SINGLE_USE_RESULTS` is active, this is the delay before results are removed from the task registry. |
|  | `DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT` | `604800` (7 days) | The maximum time for processing a document. |
|  | `DOCLING_SERVE_MAX_NUM_PAGES` |  | The maximum number of pages for a document to be processed. |
|  | `DOCLING_SERVE_MAX_FILE_SIZE` |  | The maximum file size for a document to be processed. |
|  | `DOCLING_SERVE_ALLOWED_SOURCE_TYPES` | `null` (built-in API sources) | List of allowed batch source kinds. Accepts a JSON array or comma-separated string. Registered plugin sources require explicit inclusion; `local_path` is never available remotely. |
|  | `DOCLING_SERVE_ALLOWED_TARGET_TYPES` | `null` (built-in API targets) | List of allowed target kinds. Accepts a JSON array or comma-separated string. Registered plugin targets require explicit inclusion and artifact result mode; `local_path` is never available remotely. |
|  | `DOCLING_SERVE_SYNC_POLL_INTERVAL` | `2` | Number of seconds to sleep between polling the task status in the sync endpoints. |
|  | `DOCLING_SERVE_MAX_SYNC_WAIT` | `120` | Max number of seconds a synchronous endpoint is waiting for the task completion. |
|  | `DOCLING_SERVE_LOAD_MODELS_AT_BOOT` | `True` | If enabled, the models for the default options will be loaded at boot. |
|  | `DOCLING_SERVE_OPTIONS_CACHE_SIZE` | `2` | How many DocumentConveter objects (including their loaded models) to keep in the cache. |
|  | `DOCLING_SERVE_QUEUE_MAX_SIZE` | | Size of the pages queue. Potentially so many pages opened at the same time. |
|  | `DOCLING_SERVE_OCR_BATCH_SIZE` | | Batch size for the OCR stage. |
|  | `DOCLING_SERVE_LAYOUT_BATCH_SIZE` | | Batch size for the layout detection stage. |
|  | `DOCLING_SERVE_TABLE_BATCH_SIZE` | | Batch size for the table structure stage. |
|  | `DOCLING_SERVE_BATCH_POLLING_INTERVAL_SECONDS` | | Wait time for gathering pages before starting a stage processing. |
|  | `DOCLING_SERVE_CORS_ORIGINS` | `["*"]` | A list of origins that should be permitted to make cross-origin requests. |
|  | `DOCLING_SERVE_CORS_METHODS` | `["*"]` | A list of HTTP methods that should be allowed for cross-origin requests. |
|  | `DOCLING_SERVE_CORS_HEADERS` | `["*"]` | A list of HTTP request headers that should be supported for cross-origin requests. |
|  | `DOCLING_SERVE_API_KEY` | | If specified, all the API requests must contain the header `X-Api-Key` with this value. |
|  | `DOCLING_SERVE_ENG_KIND` | `local` | The compute engine to use for the async tasks. Possible values are `local`, `rq` and `ray`. See below for more configurations of the engines. |

### Configuration File Support

Docling Serve supports loading configuration from YAML or JSON files. This is useful for complex configurations with nested structures.

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_SERVE_CONFIG_FILE` | | Path to a YAML or JSON configuration file. Environment variables take precedence over config file values. See [examples/config.yaml](../examples/config.yaml) and [examples/config.json](../examples/config.json) for examples. |

**Priority Order:** Environment variables > Config file > Defaults

### Logging Configuration

Docling Serve supports both traditional text-based logging and structured JSON logging. JSON logging is particularly useful for production deployments, log aggregation systems, and observability platforms.

#### JSON Logging

Enable JSON logging by setting:

```bash
export DOCLING_SERVE_LOG_FORMAT=json
```

Or in a YAML config file:

```yaml
log_format: json
```

**JSON Log Format Example:**

```json
{
  "timestamp": "2026-05-27T13:11:27.767Z",
  "level": "INFO",
  "logger": "docling_serve.app",
  "message": "Processing document",
  "RequestID": "req-123",
  "UserID": "user-456"
}
```

#### Request Header Propagation

HTTP request headers can be automatically propagated to all logs during a request's lifecycle. This is useful for tracking requests across distributed systems, correlating logs, and debugging.

**Configuration:**

```bash
# Set the header prefix (default: X-Docling-Log-)
export DOCLING_SERVE_LOG_HEADER_PREFIX="X-Docling-Log-"
```

**Usage Example:**

When making a request with custom headers:

```bash
curl -H "X-Docling-Log-RequestID: req-abc-123" \
     -H "X-Docling-Log-UserID: user-xyz-456" \
     -H "X-Docling-Log-TraceID: trace-789" \
     http://localhost:5001/v1/convert
```

All logs generated during this request will include:

```json
{
  "timestamp": "2026-05-27T13:11:27.767Z",
  "level": "INFO",
  "logger": "docling_serve.app",
  "message": "Starting document conversion",
  "RequestID": "req-abc-123",
  "UserID": "user-xyz-456",
  "TraceID": "trace-789"
}
```

**Key Features:**

- Headers are matched case-insensitively
- The prefix is stripped from the header name in logs (e.g., `X-Docling-Log-RequestID` → `RequestID`)
- Works with both JSON and text log formats (though structured fields are only visible in JSON)
- Thread-safe and works correctly with async operations
- Context is automatically cleared after each request

**Common Use Cases:**

- **Request Tracking:** Add `X-Docling-Log-RequestID` to track requests across services
- **User Context:** Add `X-Docling-Log-UserID` to associate logs with specific users
- **Distributed Tracing:** Add `X-Docling-Log-TraceID` for correlation with tracing systems
- **Session Tracking:** Add `X-Docling-Log-SessionID` to group related requests

### DoclingConverterManager Configuration

The following options control the behavior of the Docling converter, including preset management and engine restrictions.

#### VLM Pipeline Control

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_SERVE_DEFAULT_VLM_PRESET` | `granite_docling` | Default VLM preset to use when user specifies "default". |
| `DOCLING_SERVE_ALLOWED_VLM_PRESETS` | `null` (all allowed) | List of allowed VLM preset IDs. Accepts JSON array (`'["preset1", "preset2"]'`) or comma-separated string (`preset1,preset2`). When set, only these presets can be used. |
| `DOCLING_SERVE_CUSTOM_VLM_PRESETS` | `{}` | Custom VLM presets defined by admin. Must be a JSON object mapping preset IDs to VlmConvertOptions. Example: `'{"my_preset": {"engine": "openai", "model": "gpt-4-vision"}}'` |
| `DOCLING_SERVE_ALLOWED_VLM_ENGINES` | `null` (all allowed) | List of allowed VLM engine types. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_ALLOW_CUSTOM_VLM_CONFIG` | `false` | Whether users can specify fully custom VLM engine configurations. |

#### Picture Description Control

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_SERVE_DEFAULT_PICTURE_DESCRIPTION_PRESET` | `smolvlm` | Default picture description preset. |
| `DOCLING_SERVE_ALLOWED_PICTURE_DESCRIPTION_PRESETS` | `null` (all allowed) | List of allowed picture description preset IDs. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_CUSTOM_PICTURE_DESCRIPTION_PRESETS` | `{}` | Custom picture description presets. Must be a JSON object. |
| `DOCLING_SERVE_ALLOWED_PICTURE_DESCRIPTION_ENGINES` | `null` (all allowed) | List of allowed picture description engine types. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_ALLOW_CUSTOM_PICTURE_DESCRIPTION_CONFIG` | `false` | Whether users can specify custom picture description configurations. |

#### Code/Formula Control

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_SERVE_DEFAULT_CODE_FORMULA_PRESET` | `default` | Default code/formula preset. |
| `DOCLING_SERVE_ALLOWED_CODE_FORMULA_PRESETS` | `null` (all allowed) | List of allowed code/formula preset IDs. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_CUSTOM_CODE_FORMULA_PRESETS` | `{}` | Custom code/formula presets. Must be a JSON object. |
| `DOCLING_SERVE_ALLOWED_CODE_FORMULA_ENGINES` | `null` (all allowed) | List of allowed code/formula engine types. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_ALLOW_CUSTOM_CODE_FORMULA_CONFIG` | `false` | Whether users can specify custom code/formula configurations. |

#### Table Structure Control

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_SERVE_DEFAULT_TABLE_STRUCTURE_KIND` | `docling_tableformer` | Default table structure kind used when user doesn't provide custom config. |
| `DOCLING_SERVE_ALLOWED_TABLE_STRUCTURE_KINDS` | `null` (all allowed) | List of allowed table structure kinds. The default kind is always implicitly allowed. Accepts JSON array or comma-separated string. Use this to block specific plugin kinds for security or policy reasons. |
| `DOCLING_SERVE_DEFAULT_TABLE_STRUCTURE_PRESET` | `tableformer_v1_accurate` | Default table structure preset to use when user specifies "default". |
| `DOCLING_SERVE_ALLOWED_TABLE_STRUCTURE_PRESETS` | `null` (all allowed) | List of allowed table structure preset IDs. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_CUSTOM_TABLE_STRUCTURE_PRESETS` | `{}` | Custom table structure presets. Must be a JSON object mapping preset IDs to table structure options with 'kind' field. |

#### Layout Control

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_SERVE_DEFAULT_LAYOUT_KIND` | `docling_layout_default` | Default layout kind used when user doesn't provide custom config. |
| `DOCLING_SERVE_ALLOWED_LAYOUT_KINDS` | `null` (all allowed) | List of allowed layout kinds. The default kind is always implicitly allowed. Accepts JSON array or comma-separated string. Use this to block specific plugin kinds for security or policy reasons. |
| `DOCLING_SERVE_DEFAULT_LAYOUT_PRESET` | `docling_layout_default` | Default layout preset to use when user specifies "default". |
| `DOCLING_SERVE_ALLOWED_LAYOUT_PRESETS` | `null` (all allowed) | List of allowed layout preset IDs. Accepts JSON array or comma-separated string. |
| `DOCLING_SERVE_CUSTOM_LAYOUT_PRESETS` | `{}` | Custom layout presets. Must be a JSON object mapping preset IDs to layout options with 'kind' field. |

**Configuration Examples:**

Using JSON arrays in environment variables:
```bash
export DOCLING_SERVE_ALLOWED_VLM_PRESETS='["granite_docling", "custom_preset"]'
export DOCLING_SERVE_CUSTOM_VLM_PRESETS='{"my_preset": {"engine": "openai"}}'
```

Using comma-separated strings (for lists only):
```bash
export DOCLING_SERVE_ALLOWED_VLM_PRESETS="granite_docling,custom_preset"
export DOCLING_SERVE_ALLOWED_LAYOUT_KINDS="docling_layout_default,layout_object_detection"
```

Using a configuration file (recommended for complex setups):
```bash
export DOCLING_SERVE_CONFIG_FILE=config.yaml
```

See [examples/config.yaml](../examples/config.yaml) for a complete configuration file example.

### Docling configuration

Some Docling settings, mostly about performance, are exposed as environment variable which can be used also when running Docling Serve.

| ENV | Default | Description |
| ----|---------|-------------|
| `DOCLING_NUM_THREADS` | `4` | Number of concurrent threads used for the `torch` CPU execution. |
| `DOCLING_DEVICE` | | Device used for the model execution. Valid values are `cpu`, `cuda`, `mps`. When unset, the best device is chosen. For CUDA-enabled environments, you can choose which GPU using the syntax `cuda:0`, `cuda:1`, ... |
| `DOCLING_PERF_PAGE_BATCH_SIZE` | `4` | Number of pages processed in the same batch. |
| `DOCLING_PERF_ELEMENTS_BATCH_SIZE` | `8` | Number of document items/elements processed in the same batch during enrichment. |
| `DOCLING_DEBUG_PROFILE_PIPELINE_TIMINGS` | `false` | When enabled, Docling will provide detailed timings information. |


### Compute engine

Docling Serve can be deployed with several possible of compute engine.
The selected compute engine will be running all the async jobs.

#### Local engine

The following table describes the options to configure the Docling Serve local engine.

| ENV | Default | Description |
|-----|---------|-------------|
| `DOCLING_SERVE_ENG_LOC_NUM_WORKERS` | 2 | Number of workers/threads processing the incoming tasks. |
| `DOCLING_SERVE_ENG_LOC_SHARE_MODELS` | False | If true, each process will share the same models among all thread workers. Otherwise, one instance of the models is allocated for each worker thread. |

#### RQ engine

The following table describes the options to configure the Docling Serve RQ engine.

| ENV | Default | Description |
|-----|---------|-------------|
| `DOCLING_SERVE_ENG_RQ_REDIS_URL` | (required) | The connection Redis url, e.g. `redis://localhost:6373/` |
| `DOCLING_SERVE_ENG_RQ_QUEUE_NAME` | `convert` | The RQ queue name used by API instances and workers. Set this to route jobs to a non-default queue. |
| `DOCLING_SERVE_ENG_RQ_RESULTS_PREFIX` | `docling:results` | The prefix used for storing the results in Redis. |
| `DOCLING_SERVE_ENG_RQ_SUB_CHANNEL` | `docling:updates` | The channel key name used for storing communicating updates between the workers and the orchestrator. |
| `DOCLING_SERVE_ENG_RQ_RESULTS_TTL` | `14400` (4 hours) | Time To Live (in seconds) for RQ job results in Redis. This controls how long job results are kept before being automatically deleted. |
| `DOCLING_SERVE_ENG_RQ_JOB_TIMEOUT` | `14400` (4 hours) | Maximum runtime (in seconds) of a single RQ job before the worker aborts it with a `JobTimeoutException`. Raise it for long conversions (large documents, CPU-only inference, enrichments), or set `-1` to disable the limit and rely on `DOCLING_SERVE_MAX_DOCUMENT_TIMEOUT` / the request's `document_timeout` instead. The value is baked into each job when it is enqueued, so it must be set on the instances serving the API, not on the RQ workers, and jobs already in the queue keep the value they were enqueued with. Do not set `0` — RQ treats it as unset and falls back to its own 180 s default. |
| `DOCLING_SERVE_ENG_RQ_REDIS_MAX_CONNECTIONS` | `50` | Maximum number of connections in the Redis connection pool. Increase this value when scaling to many RQ workers (e.g., 100 for 10+ workers). |
| `DOCLING_SERVE_ENG_RQ_REDIS_SOCKET_TIMEOUT` | `None` | Socket timeout in seconds for Redis operations. If not set, uses Redis client default. Set to a value (e.g., 5.0) if you experience timeout issues. |
| `DOCLING_SERVE_ENG_RQ_REDIS_SOCKET_CONNECT_TIMEOUT` | `None` | Socket connect timeout in seconds for establishing Redis connections. If not set, uses Redis client default. Set to a value (e.g., 5.0) for slow networks. |

**Scaling Recommendations for RQ Engine:**

- **Small deployments (1-4 workers):** Default settings (50 connections) are sufficient
- **Medium deployments (5-10 workers):** Set `DOCLING_SERVE_ENG_RQ_REDIS_MAX_CONNECTIONS=100`
- **Large deployments (10+ workers):** Set `DOCLING_SERVE_ENG_RQ_REDIS_MAX_CONNECTIONS=150-200`
- **Timeout settings:** Only set if experiencing connection issues. Start with 5.0 seconds for both timeouts.
- Ensure your Redis server's `maxclients` setting can accommodate all connections from all docling-serve instances and RQ workers

### Gradio UI

When using Gradio UI and using the option to output conversion as file, Gradio uses cache to prevent files to be overwritten ([more info here](https://www.gradio.app/guides/file-access#the-gradio-cache)), and we defined the cache clean frequency of one hour to clean files older than 10hours. For situations that files need to be available to download from UI older than 10 hours, there is two options:

- Increase the older age of files to clean [here](https://github.com/docling-project/docling-serve/blob/main/docling_serve/gradio_ui.py#L483) to suffice the age desired;
- Or set the clean up manually by defining the temporary dir of Gradio to use the same as `DOCLING_SERVE_SCRATCH_PATH` absolute path. This can be achieved by setting the environment variable `GRADIO_TEMP_DIR`, that can be done via command line `export GRADIO_TEMP_DIR="<same_path_as_scratch>"` or in `Dockerfile` using `ENV GRADIO_TEMP_DIR="<same_path_as_scratch>"`. After this, set the clean of cache to `None` [here](https://github.com/docling-project/docling-serve/blob/main/docling_serve/gradio_ui.py#L483). Now, the clean up of `DOCLING_SERVE_SCRATCH_PATH` will also clean the Gradio temporary dir. (If you use this option, please remember when reversing changes to remove the environment variable `GRADIO_TEMP_DIR`, otherwise may lead to files not be available to download).

### Telemetry

THe following table describes the telemetry options for the Docling Serve app. Some deployment examples are available in [examples/OTEL.md](../examples/OTEL.md).

ENV | Default | Description |
|-----|---------|-------------|
| `DOCLING_SERVE_OTEL_ENABLE_METRICS` | true | Enable metrics collection. |
| `DOCLING_SERVE_OTEL_ENABLE_TRACES` | false | Enable trace collection. Requires a valid value for `OTEL_EXPORTER_OTLP_ENDPOINT`. |
| `DOCLING_SERVE_OTEL_ENABLE_PROMETHEUS` | true | Enable Prometheus /metrics endpoint. |
| `DOCLING_SERVE_OTEL_ENABLE_OTLP_METRICS` | `false` | Enable OTLP metrics export. |
| `DOCLING_SERVE_OTEL_SERVICE_NAME` | docling-serve | Service identification. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` |  | OTLP endpoint (for traces and optional metrics). |
| `DOCLING_SERVE_METRICS_PORT` | `None` | Enable serving /metrics endpoint on a separate port. |
