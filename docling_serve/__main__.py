import importlib.metadata
import logging
import os
import platform
import sys
import warnings
from pathlib import Path
from typing import Annotated, Any, Union

import typer
import uvicorn
from rich.console import Console

from docling_serve.settings import docling_serve_settings, uvicorn_settings

warnings.filterwarnings(action="ignore", category=UserWarning, module="pydantic|torch")
warnings.filterwarnings(action="ignore", category=FutureWarning, module="easyocr")


err_console = Console(stderr=True)
console = Console()

app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = logging.getLogger(__name__)


def version_callback(value: bool) -> None:
    if value:
        docling_serve_version = importlib.metadata.version("docling-serve")
        docling_jobkit_version = importlib.metadata.version("docling-jobkit")
        docling_version = importlib.metadata.version("docling-slim")
        docling_core_version = importlib.metadata.version("docling-core")
        docling_ibm_models_version = importlib.metadata.version("docling-ibm-models")
        docling_parse_version = importlib.metadata.version("docling-parse")
        platform_str = platform.platform()
        py_impl_version = sys.implementation.cache_tag
        py_lang_version = platform.python_version()
        console.print(f"Docling Serve version: {docling_serve_version}")
        console.print(f"Docling Jobkit version: {docling_jobkit_version}")
        console.print(f"Docling version: {docling_version}")
        console.print(f"Docling Core version: {docling_core_version}")
        console.print(f"Docling IBM Models version: {docling_ibm_models_version}")
        console.print(f"Docling Parse version: {docling_parse_version}")
        console.print(f"Python: {py_impl_version} ({py_lang_version})")
        console.print(f"Platform: {platform_str}")
        raise typer.Exit()


@app.callback()
def callback(
    version: Annotated[
        Union[bool, None],
        typer.Option(help="Show the version and exit.", callback=version_callback),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Set the verbosity level. -v for info logging, -vv for debug logging.",
        ),
    ] = 0,
) -> None:
    from docling_serve.logging_config import setup_logging

    # Priority: CLI flag > ENV variable > default (WARNING)
    if verbose > 0:
        # CLI flag takes precedence
        log_level = "INFO" if verbose == 1 else "DEBUG"
    elif docling_serve_settings.log_level:
        # Use ENV variable if CLI flag not provided
        log_level = docling_serve_settings.log_level.value
    else:
        # Default to WARNING
        log_level = "WARNING"

    # Setup logging with configured format
    setup_logging(
        log_format=docling_serve_settings.log_format.value,
        log_level=log_level,
        header_prefix=docling_serve_settings.log_header_prefix,
    )


def _run(
    *,
    command: str,
    # Docling serve parameters
    artifacts_path: Path | None,
    enable_ui: bool,
) -> None:
    server_type = "development" if command == "dev" else "production"

    console.print(f"Starting {server_type} server 🚀")

    run_subprocess = (
        uvicorn_settings.workers is not None and uvicorn_settings.workers > 1
    ) or uvicorn_settings.reload

    run_ssl = (
        uvicorn_settings.ssl_certfile is not None
        and uvicorn_settings.ssl_keyfile is not None
    )

    if run_subprocess:
        # With reload or multiple workers, uvicorn starts the server through
        # multiprocessing "spawn". The child re-imports docling_serve.settings
        # and rebuilds the settings from the environment, so the assignments
        # below never reach it. Hand the CLI values over through the very
        # environment variables the settings model reads.
        if artifacts_path is not None:
            os.environ["DOCLING_SERVE_ARTIFACTS_PATH"] = str(artifacts_path)
        os.environ["DOCLING_SERVE_ENABLE_UI"] = str(enable_ui).lower()

    # Propagate the settings to the app settings
    docling_serve_settings.artifacts_path = artifacts_path
    docling_serve_settings.enable_ui = enable_ui

    # Print documentation
    protocol = "https" if run_ssl else "http"
    url = f"{protocol}://{uvicorn_settings.host}:{uvicorn_settings.port}"
    url_docs = f"{url}/docs"
    url_scalar = f"{url}/scalar"
    url_ui = f"{url}/ui"

    console.print("")
    console.print(f"Server started at [link={url}]{url}[/]")
    console.print(f"Documentation at [link={url_docs}]{url_docs}[/]")
    console.print(f"Scalar docs at [link={url_docs}]{url_scalar}[/]")
    if docling_serve_settings.enable_ui:
        console.print(f"UI at [link={url_ui}]{url_ui}[/]")

    if command == "dev":
        console.print("")
        console.print(
            "Running in development mode, for production use: "
            "[bold]docling-serve run[/]",
        )

    console.print("")
    console.print("Logs:")

    # Launch the server
    # Disable uvicorn's default logging config so our custom logging is used
    uvicorn.run(
        app="docling_serve.app:create_app",
        factory=True,
        host=uvicorn_settings.host,
        port=uvicorn_settings.port,
        reload=uvicorn_settings.reload,
        workers=uvicorn_settings.workers,
        root_path=uvicorn_settings.root_path,
        proxy_headers=uvicorn_settings.proxy_headers,
        timeout_keep_alive=uvicorn_settings.timeout_keep_alive,
        ssl_certfile=uvicorn_settings.ssl_certfile,
        ssl_keyfile=uvicorn_settings.ssl_keyfile,
        ssl_keyfile_password=uvicorn_settings.ssl_keyfile_password,
        log_config=None,  # Disable uvicorn's logging config to use our custom setup
    )


@app.command()
def dev(
    *,
    # uvicorn options
    host: Annotated[
        str,
        typer.Option(
            help=(
                "The host to serve on. For local development in localhost "
                "use [blue]127.0.0.1[/blue]. To enable public access, "
                "e.g. in a container, use all the IP addresses "
                "available with [blue]0.0.0.0[/blue]."
            )
        ),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(help="The port to serve on."),
    ] = uvicorn_settings.port,
    reload: Annotated[
        bool,
        typer.Option(
            help=(
                "Enable auto-reload of the server when (code) files change. "
                "This is [bold]resource intensive[/bold], "
                "use it only during development."
            )
        ),
    ] = True,
    root_path: Annotated[
        str,
        typer.Option(
            help=(
                "The root path is used to tell your app that it is being served "
                "to the outside world with some [bold]path prefix[/bold] "
                "set up in some termination proxy or similar."
            )
        ),
    ] = uvicorn_settings.root_path,
    proxy_headers: Annotated[
        bool,
        typer.Option(
            help=(
                "Enable/Disable X-Forwarded-Proto, X-Forwarded-For, "
                "X-Forwarded-Port to populate remote address info."
            )
        ),
    ] = uvicorn_settings.proxy_headers,
    timeout_keep_alive: Annotated[
        int, typer.Option(help="Timeout for the server response.")
    ] = uvicorn_settings.timeout_keep_alive,
    ssl_certfile: Annotated[
        Path | None, typer.Option(help="SSL certificate file")
    ] = uvicorn_settings.ssl_certfile,
    ssl_keyfile: Annotated[
        Path | None, typer.Option(help="SSL key file")
    ] = uvicorn_settings.ssl_keyfile,
    ssl_keyfile_password: Annotated[
        str | None, typer.Option(help="SSL keyfile password")
    ] = uvicorn_settings.ssl_keyfile_password,
    # docling options
    artifacts_path: Annotated[
        Path | None,
        typer.Option(
            help=(
                "If set to a valid directory, "
                "the model weights will be loaded from this path."
            )
        ),
    ] = docling_serve_settings.artifacts_path,
    enable_ui: Annotated[bool, typer.Option(help="Enable the development UI.")] = True,
) -> Any:
    """
    Run a [bold]Docling Serve[/bold] app in [yellow]development[/yellow] mode. 🧪

    This is equivalent to [bold]docling-serve run[/bold] but with [bold]reload[/bold]
    enabled and listening on the [blue]127.0.0.1[/blue] address.

    Options can be set also with the corresponding ENV variable, with the exception
    of --enable-ui, --host and --reload.
    """

    uvicorn_settings.host = host
    uvicorn_settings.port = port
    uvicorn_settings.reload = reload
    uvicorn_settings.root_path = root_path
    uvicorn_settings.proxy_headers = proxy_headers
    uvicorn_settings.timeout_keep_alive = timeout_keep_alive
    uvicorn_settings.ssl_certfile = ssl_certfile
    uvicorn_settings.ssl_keyfile = ssl_keyfile
    uvicorn_settings.ssl_keyfile_password = ssl_keyfile_password

    _run(
        command="dev",
        artifacts_path=artifacts_path,
        enable_ui=enable_ui,
    )


@app.command()
def run(
    *,
    host: Annotated[
        str,
        typer.Option(
            help=(
                "The host to serve on. For local development in localhost "
                "use [blue]127.0.0.1[/blue]. To enable public access, "
                "e.g. in a container, use all the IP addresses "
                "available with [blue]0.0.0.0[/blue]."
            )
        ),
    ] = uvicorn_settings.host,
    port: Annotated[
        int,
        typer.Option(help="The port to serve on."),
    ] = uvicorn_settings.port,
    reload: Annotated[
        bool,
        typer.Option(
            help=(
                "Enable auto-reload of the server when (code) files change. "
                "This is [bold]resource intensive[/bold], "
                "use it only during development."
            )
        ),
    ] = uvicorn_settings.reload,
    workers: Annotated[
        Union[int, None],
        typer.Option(
            help=(
                "Use multiple worker processes. "
                "Mutually exclusive with the --reload flag."
            )
        ),
    ] = uvicorn_settings.workers,
    root_path: Annotated[
        str,
        typer.Option(
            help=(
                "The root path is used to tell your app that it is being served "
                "to the outside world with some [bold]path prefix[/bold] "
                "set up in some termination proxy or similar."
            )
        ),
    ] = uvicorn_settings.root_path,
    proxy_headers: Annotated[
        bool,
        typer.Option(
            help=(
                "Enable/Disable X-Forwarded-Proto, X-Forwarded-For, "
                "X-Forwarded-Port to populate remote address info."
            )
        ),
    ] = uvicorn_settings.proxy_headers,
    timeout_keep_alive: Annotated[
        int, typer.Option(help="Timeout for the server response.")
    ] = uvicorn_settings.timeout_keep_alive,
    ssl_certfile: Annotated[
        Path | None, typer.Option(help="SSL certificate file")
    ] = uvicorn_settings.ssl_certfile,
    ssl_keyfile: Annotated[
        Path | None, typer.Option(help="SSL key file")
    ] = uvicorn_settings.ssl_keyfile,
    ssl_keyfile_password: Annotated[
        str | None, typer.Option(help="SSL keyfile password")
    ] = uvicorn_settings.ssl_keyfile_password,
    # docling options
    artifacts_path: Annotated[
        Path | None,
        typer.Option(
            help=(
                "If set to a valid directory, "
                "the model weights will be loaded from this path."
            )
        ),
    ] = docling_serve_settings.artifacts_path,
    enable_ui: Annotated[
        bool, typer.Option(help="Enable the development UI.")
    ] = docling_serve_settings.enable_ui,
) -> Any:
    """
    Run a [bold]Docling Serve[/bold] app in [green]production[/green] mode. 🚀

    This is equivalent to [bold]docling-serve dev[/bold] but with [bold]reload[/bold]
    disabled and listening on the [blue]0.0.0.0[/blue] address.

    Options can be set also with the corresponding ENV variable, e.g. UVICORN_PORT
    or DOCLING_SERVE_ENABLE_UI.
    """

    uvicorn_settings.host = host
    uvicorn_settings.port = port
    uvicorn_settings.reload = reload
    uvicorn_settings.workers = workers
    uvicorn_settings.root_path = root_path
    uvicorn_settings.proxy_headers = proxy_headers
    uvicorn_settings.timeout_keep_alive = timeout_keep_alive
    uvicorn_settings.ssl_certfile = ssl_certfile
    uvicorn_settings.ssl_keyfile = ssl_keyfile
    uvicorn_settings.ssl_keyfile_password = ssl_keyfile_password

    _run(
        command="run",
        artifacts_path=artifacts_path,
        enable_ui=enable_ui,
    )


@app.command()
def rq_worker() -> Any:
    """
    Run the [bold]Docling JobKit[/bold] RQ worker.
    """
    import tempfile
    from pathlib import Path

    from docling_jobkit.convert.manager import (
        DoclingConverterManagerConfig,
    )
    from docling_jobkit.orchestrators.rq.orchestrator import RQOrchestrator

    from docling_serve.logging_config import setup_logging
    from docling_serve.orchestrator_factory import _build_rq_config
    from docling_serve.rq_instrumentation import setup_rq_worker_instrumentation
    from docling_serve.rq_worker_instrumented import InstrumentedRQWorker

    # Configure logging for RQ worker
    log_level = (
        docling_serve_settings.log_level.value
        if docling_serve_settings.log_level
        else "WARNING"
    )
    setup_logging(
        log_format=docling_serve_settings.log_format.value,
        log_level=log_level,
        header_prefix=docling_serve_settings.log_header_prefix,
    )

    # Set up OpenTelemetry for the worker process
    if docling_serve_settings.otel_enable_traces:
        setup_rq_worker_instrumentation()

    rq_config = _build_rq_config()

    cm_config = DoclingConverterManagerConfig(
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
        # Extraction Control
        default_extraction_preset=docling_serve_settings.default_extraction_preset,
        allowed_extraction_presets=docling_serve_settings.allowed_extraction_presets,
        allowed_extraction_engines=docling_serve_settings.allowed_extraction_engines,
        allowed_extraction_formats=docling_serve_settings.allowed_extraction_formats,
        allow_custom_extraction_config=docling_serve_settings.allow_custom_extraction_config,
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

    # Create worker with instrumentation
    scratch_dir = rq_config.scratch_dir or Path(tempfile.mkdtemp(prefix="docling_"))
    redis_conn, rq_queue = RQOrchestrator.make_rq_queue(rq_config)

    worker = InstrumentedRQWorker(
        [rq_queue],
        connection=redis_conn,
        orchestrator_config=rq_config,
        cm_config=cm_config,
        scratch_dir=scratch_dir,
    )

    worker.work()


def main() -> None:
    app()


# Launch the CLI when calling python -m docling_serve
if __name__ == "__main__":
    main()
