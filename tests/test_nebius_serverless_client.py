# SPDX-FileCopyrightText: The Docling Contributors
# SPDX-License-Identifier: MIT

"""Offline contract tests for the deployment example; no GPU/cloud credentials."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import yaml

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/deploy-examples/nebius-serverless"
spec = importlib.util.spec_from_file_location("nebius_example", EXAMPLE / "client.py")
assert spec and spec.loader
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)
TASK = "74d3b87c-8246-4d1a-9f38-136346982f93"
BASE = "https://endpoint.example.test"
HOST = "storage.example.test"
BUCKET = "example-results"
PREFIX = f"docling-example/default/20260911/{TASK}/source"


def result():
    return {
        "num_converted": 1,
        "num_succeeded": 1,
        "num_partially_succeeded": 0,
        "num_failed": 0,
        "documents": [
            {
                "status": "success",
                "errors": [],
                "artifacts": [
                    {
                        "artifact_type": "markdown",
                        "uri": f"https://{HOST}/{BUCKET}/{PREFIX}/document.md?signature=secret",
                    },
                    {
                        "artifact_type": "json",
                        "uri": f"https://{HOST}/{BUCKET}/{PREFIX}/document.json?signature=secret",
                    },
                ],
            }
        ],
    }


def storage(request):
    assert "authorization" not in request.headers
    assert "x-api-key" not in request.headers
    body = (
        b'{"schema_name":"DoclingDocument"}'
        if request.url.path.endswith(".json")
        else b"# Converted document\n"
    )
    return httpx.Response(200, content=body)


async def exercise(tmp_path, api_handler, storage_handler=storage):
    source = tmp_path / "input.html"
    source.write_text("<h1>Example</h1>")
    output = tmp_path / "output"
    output.mkdir()
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(api_handler)) as api,
        httpx.AsyncClient(transport=httpx.MockTransport(storage_handler)) as artifacts,
    ):
        client = example.ConversionClient(api, artifacts, BASE, output, HOST, BUCKET)
        receipt = await client.submit(source, "endpoint-test")
        return await client.collect(receipt, poll_interval=0)


@pytest.mark.asyncio
async def test_submit_poll_download_and_offline_resume(tmp_path):
    calls = []
    polls = iter(["pending", "started", "success"])

    def api(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = request.read()
            for item in (
                b'filename="input.html"',
                b"presigned_url",
                b"easyocr",
                b'name="ocr_preset"',
                b'"to_formats"',
            ):
                assert item in body
            # Receipt must already exist before the request is dispatched.
            assert (
                json.loads((tmp_path / "output/receipt.json").read_text())["state"]
                == "submission_unknown"
            )
            return httpx.Response(200, json={"task_id": TASK})
        if "/poll/" in request.url.path:
            return httpx.Response(
                200, json={"task_id": TASK, "task_status": next(polls)}
            )
        return httpx.Response(200, json=result())

    manifest = await exercise(tmp_path, api)
    assert manifest["state"] == "complete"
    assert len([c for c in calls if c[0] == "POST"]) == 1
    output = tmp_path / "output"
    receipt = example.load_receipt(output, BASE, "endpoint-test", HOST, BUCKET)
    assert example.verify_complete(output, receipt) == manifest
    assert "signature" not in (output / "receipt.json").read_text()
    assert "signature" not in (output / "manifest.json").read_text()
    (output / "document.md").write_text("changed")
    with pytest.raises(example.ExampleError, match="changed"):
        example.verify_complete(output, receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "http500", "malformed", "wrong_id", "cancelled"]
)
async def test_unknown_submission_is_never_replayed(tmp_path, failure):
    count = 0

    def api(request):
        nonlocal count
        count += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("contains a credential which must not be printed")
        if failure == "cancelled":
            raise asyncio.CancelledError
        if failure == "http500":
            return httpx.Response(500)
        return httpx.Response(
            200,
            content=b"garbage"
            if failure == "malformed"
            else b'{"task_id":"../../bad"}',
        )

    with pytest.raises((example.ExampleError, ValueError, asyncio.CancelledError)):
        await exercise(tmp_path, api)
    assert count == 1
    with pytest.raises(example.ExampleError, match="unknown"):
        example.load_receipt(tmp_path / "output", BASE, "endpoint-test", HOST, BUCKET)
    assert not (tmp_path / "output/manifest.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_poll_errors_do_not_replay_submission(tmp_path, status):
    calls = []

    def api(request):
        calls.append(request.method)
        return (
            httpx.Response(200, json={"task_id": TASK})
            if request.method == "POST"
            else httpx.Response(status)
        )

    with pytest.raises(example.ExampleError, match=str(status)):
        await exercise(tmp_path, api)
    assert calls == ["POST", "GET"]
    assert (
        example.load_receipt(tmp_path / "output", BASE, "endpoint-test", HOST, BUCKET)[
            "task_id"
        ]
        == TASK
    )


@pytest.mark.asyncio
async def test_resume_uses_only_get(tmp_path):
    seen = []

    def api(request):
        seen.append(request.method)
        return httpx.Response(
            200,
            json={"task_id": TASK, "task_status": "success"}
            if "/poll/" in request.url.path
            else result(),
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(api)) as api_client,
        httpx.AsyncClient(transport=httpx.MockTransport(storage)) as s3,
    ):
        client = example.ConversionClient(api_client, s3, BASE, tmp_path, HOST, BUCKET)
        await client.collect({"task_id": TASK}, poll_interval=0)
    assert seen == ["GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["failure", "unknown_state", "partial", "document_failure", "missing_artifact"],
)
async def test_no_manifest_for_failed_or_partial_conversion(tmp_path, mode):
    payload = result()
    if mode == "partial":
        payload["num_partially_succeeded"] = 1
    if mode == "document_failure":
        payload["documents"][0]["status"] = "failure"
    if mode == "missing_artifact":
        payload["documents"][0]["artifacts"].pop()

    def api(request):
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": TASK})
        if "/poll/" in request.url.path:
            state = mode if mode in ("failure", "unknown_state") else "success"
            return httpx.Response(200, json={"task_id": TASK, "task_status": state})
        return httpx.Response(200, json=payload)

    with pytest.raises(example.ExampleError):
        await exercise(tmp_path, api)
    assert not (tmp_path / "output/manifest.json").exists()


@pytest.mark.parametrize(
    "uri",
    [
        f"http://{HOST}/{BUCKET}/{PREFIX}/a.md",
        f"https://attacker.test/{BUCKET}/{PREFIX}/a.md",
        f"https://{HOST}/other-bucket/{PREFIX}/a.md",
        f"https://{HOST}/{BUCKET}/other-prefix/default/20260911/{TASK}/source/a.md",
        f"https://{HOST}/{BUCKET}/{PREFIX}/%2E%2E",
        f"https://user:password@{HOST}/{BUCKET}/{PREFIX}/a.md",
        f"https://{HOST}:444/{BUCKET}/{PREFIX}/a.md",
    ],
)
def test_artifact_url_scope(uri):
    with pytest.raises(example.ExampleError):
        example.artifact_identity(uri, HOST, BUCKET, TASK)


def test_virtual_host_bucket_url():
    assert example.artifact_identity(
        f"https://{BUCKET}.{HOST}/{PREFIX}/a.md", HOST, BUCKET, TASK
    ) == {"bucket": BUCKET, "key": f"{PREFIX}/a.md"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["expired", "redirect", "oversized", "truncated", "corrupt_json", "compressed"],
)
async def test_artifact_failure_leaves_no_completion(tmp_path, mode, monkeypatch):
    monkeypatch.setattr(example, "MAX_ARTIFACT", 64)

    def api(request):
        return httpx.Response(
            200,
            json={"task_id": TASK, "task_status": "success"}
            if "/result/" not in request.url.path
            else result(),
        )

    def bad_storage(request):
        if mode == "expired":
            return httpx.Response(403)
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.test"})
        if mode == "oversized":
            return httpx.Response(200, content=b"x" * 65)
        if mode == "truncated":
            return httpx.Response(
                200, content=b"short", headers={"Content-Length": "20"}
            )
        if mode == "compressed":
            return httpx.Response(
                200, content=b"x", headers={"Content-Encoding": "identity, identity"}
            )
        return httpx.Response(200, content=b"not json")

    with pytest.raises((example.ExampleError, ValueError, httpx.DecodingError)):
        await exercise(tmp_path, api, bad_storage)
    assert not (tmp_path / "output/manifest.json").exists()
    assert not list((tmp_path / "output").glob("*.part"))


@pytest.mark.asyncio
async def test_deadline_preserves_acknowledged_task(tmp_path):
    async def api(request):
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": TASK})
        await asyncio.sleep(10)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(exercise(tmp_path, api), timeout=0.05)
    assert (
        example.load_receipt(tmp_path / "output", BASE, "endpoint-test", HOST, BUCKET)[
            "task_id"
        ]
        == TASK
    )


def test_single_writer_and_existing_output(tmp_path):
    output = tmp_path / "result"
    with example.locked_output(output, False):
        with pytest.raises(example.ExampleError, match="Another client"):
            with example.locked_output(output, True):
                pytest.fail("Acquired duplicate lock")
    with pytest.raises(FileExistsError):
        with example.locked_output(output, False):
            pytest.fail("Reused output")


def test_config_keys_and_secret_overrides(monkeypatch):
    from docling_serve.settings import DoclingServeSettings

    data = yaml.safe_load((EXAMPLE / "docling-config.yaml").read_text())
    assert not set(data) - set(DoclingServeSettings.model_fields)
    monkeypatch.setenv(
        "DOCLING_SERVE_CONFIG_FILE", str(EXAMPLE / "docling-config.yaml")
    )
    monkeypatch.setenv("DOCLING_SERVE_API_KEY", "test-only")
    config = DoclingServeSettings()
    assert config.api_key == "test-only"
    assert config.scratch_path is None
    assert config.max_file_size == example.MAX_INPUT
    assert config.allowed_target_types == ["presigned_url"]


@pytest.mark.asyncio
async def test_transient_get_retries_without_reposting(tmp_path, monkeypatch):
    calls = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr(example.asyncio, "sleep", no_sleep)

    def api(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": TASK})
        if len(calls) < 4:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"task_id": TASK, "task_status": "success"}
            if "/poll/" in request.url.path
            else result(),
        )

    await exercise(tmp_path, api)
    assert calls == ["POST", "GET", "GET", "GET", "GET"]


@pytest.mark.asyncio
async def test_input_limit_rejects_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(example, "MAX_INPUT", 1)

    def api(request):
        pytest.fail("Oversized input was submitted")

    with pytest.raises(example.ExampleError, match="10 MiB"):
        await exercise(tmp_path, api)
    assert not (tmp_path / "output/receipt.json").exists()


@pytest.mark.asyncio
async def test_control_response_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(example, "MAX_JSON", 8)
    with pytest.raises(example.ExampleError, match="byte limit"):
        await exercise(tmp_path, lambda _: httpx.Response(200, content=b"x" * 9))
    assert not (tmp_path / "output/manifest.json").exists()


@pytest.mark.asyncio
async def test_download_stream_limit_and_cancellation(tmp_path, monkeypatch):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"1234"
            yield b"5678"

    monkeypatch.setattr(example, "MAX_ARTIFACT", 5)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Chunks()))
    ) as s3:
        client = example.ConversionClient(None, s3, BASE, tmp_path, HOST, BUCKET)
        with pytest.raises(example.ExampleError, match="exceeds"):
            await client.download(f"https://{HOST}/file", "document.md", "markdown")
    assert not (tmp_path / "document.md.part").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test",
        "https://example.test/v1",
        "https://example.test?token=secret",
        "https://user:secret@example.test",
    ],
)
def test_endpoint_validation(endpoint):
    with pytest.raises(example.ExampleError):
        example.validate_endpoint(endpoint)
