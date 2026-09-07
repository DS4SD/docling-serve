# SPDX-License-Identifier: MIT

"""The UI's OCR selection must reach the conversion task unchanged."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from docling_jobkit.datamodel.task import Task

from docling_serve.app import create_app
from docling_serve.orchestrator_factory import get_async_orchestrator
from docling_serve.settings import docling_serve_settings


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    pytest.importorskip("gradio")
    # UI construction loads a logo at import time. Keep it local for this test.
    (tmp_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
    )
    monkeypatch.setattr(docling_serve_settings, "static_path", tmp_path)
    from docling_serve import gradio_ui

    return gradio_ui


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(docling_serve_settings, "api_key", "")
    monkeypatch.setattr(docling_serve_settings, "enable_ui", False)
    monkeypatch.setattr(
        docling_serve_settings, "allowed_source_types", ["file", "http"]
    )
    monkeypatch.setattr(docling_serve_settings, "allowed_target_types", ["inbody"])
    orchestrator = SimpleNamespace(
        enqueue=AsyncMock(return_value=Task(task_id="test-ocr-selection")),
        get_queue_position=AsyncMock(return_value=0),
    )
    app = create_app()
    app.dependency_overrides[get_async_orchestrator] = lambda: orchestrator
    # Do not enter lifespan: the queue and document converter must not start.
    client = TestClient(app)
    try:
        yield client, orchestrator
    finally:
        client.close()


@pytest.mark.parametrize("handler", ["file", "url"])
@pytest.mark.parametrize("enabled", [False, True], ids=["ocr-off", "ocr-on"])
def test_ui_ocr_selection_reaches_conversion_task(ui, api, tmp_path, handler, enabled):
    client, orchestrator = api
    arguments = {
        "auth": "",
        "to_formats": ["md"],
        "image_export_mode": "placeholder",
        "pipeline": "standard",
        "ocr": enabled,
        "force_ocr": False,
        "ocr_engine": "auto",
        "ocr_lang": "en,fr,de,es",
        "pdf_backend": "docling_parse",
        "table_mode": "accurate",
        "heading_hierarchy": False,
        "abort_on_error": False,
        "return_as_file": False,
        "do_code_enrichment": False,
        "do_formula_enrichment": False,
        "do_picture_classification": False,
        "do_picture_description": False,
    }
    if handler == "file":
        document = tmp_path / "sample.html"
        document.write_text("<html><body>OCR selection test</body></html>")
        arguments["files"] = [SimpleNamespace(name=str(document))]
        callback = ui.process_file
    else:
        # The mocked queue never fetches this source.
        arguments["input_sources"] = "https://example.invalid/sample.html"
        callback = ui.process_url

    def submit(url, **kwargs):
        return client.post(
            httpx.URL(url).path, json=kwargs["json"], headers=kwargs["headers"]
        )

    with patch.object(ui.httpx, "post", side_effect=submit) as post:
        assert callback(**arguments) == "test-ocr-selection"

    post.assert_called_once()
    orchestrator.enqueue.assert_awaited_once()
    options = orchestrator.enqueue.call_args.kwargs["convert_options"]
    assert options.do_ocr is enabled
