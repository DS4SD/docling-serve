# SPDX-FileCopyrightText: The Docling Contributors
# SPDX-License-Identifier: MIT
"""Bounded single-document client. Requires Python 3.10+, httpx and POSIX locks.

No cloud lifecycle calls. A submission with an unknown outcome is never replayed.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx

IMAGE = "ghcr.io/docling-project/docling-serve-cu128@sha256:c57b384ba305ab70f4f8cd02582a0aa9effbc529b7838ddc324a5d299449e8d2"
MAX_INPUT = 10 * 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_ARTIFACT = 20 * 1024 * 1024
OPTIONS = {
    "to_formats": ["md", "json"],
    "image_export_mode": "placeholder",
    "do_ocr": "true",
    "ocr_preset": "easyocr",
    "target_type": "presigned_url",
}


class ExampleError(Exception):
    """Messages must never contain response bodies, credentials or signed URLs."""


def require(condition, message):
    if not condition:
        raise ExampleError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    """Replace a local receipt/manifest only after its bytes reach the filesystem."""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def locked_output(output, resume):
    if not resume:
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
    require(output.is_dir() and not output.is_symlink(), "Invalid output directory.")
    with (output / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExampleError(
                "Another client is using this output directory."
            ) from None
        yield


def validate_endpoint(endpoint):
    url = urlsplit(endpoint)
    require(
        url.scheme == "https"
        and bool(url.hostname)
        and url.port in (None, 443)
        and not url.username
        and not url.password
        and url.path in ("", "/")
        and not url.query
        and not url.fragment,
        "Endpoint must be a managed HTTPS origin without a path or query.",
    )
    return endpoint.rstrip("/")


def artifact_identity(uri, storage_host, bucket, task_id):
    url = urlsplit(uri)
    require(
        url.scheme == "https"
        and url.port in (None, 443)
        and not url.username
        and not url.password
        and not url.fragment,
        "Artifact URL must use HTTPS without user information or a fragment.",
    )
    path = unquote(url.path).lstrip("/")
    if url.hostname == storage_host:
        require(path.startswith(bucket + "/"), "Artifact bucket mismatch.")
        key = path[len(bucket) + 1 :]
    elif url.hostname == f"{bucket}.{storage_host}":
        key = path
    else:
        raise ExampleError("Artifact storage host mismatch.")
    parts = key.split("/")
    require(
        len(parts) == 6
        and parts[0:2] == ["docling-example", "default"]
        and re.fullmatch(r"\d{8}", parts[2]) is not None
        and parts[3] == task_id
        and all(p not in ("", ".", "..") and "\\" not in p for p in parts),
        "Artifact key is outside this example's task prefix.",
    )
    return {"bucket": bucket, "key": key}


async def read_bounded(response, maximum):
    body = bytearray()
    async for block in response.aiter_bytes():
        body.extend(block)
        require(len(body) <= maximum, "Response exceeds the configured byte limit.")
    return bytes(body)


class ConversionClient:
    def __init__(self, api, storage, endpoint, output, storage_host, bucket):
        self.api = api
        self.storage = storage
        self.endpoint = endpoint
        self.output = output
        self.storage_host = storage_host
        self.bucket = bucket

    async def request_json(self, method, path, **kwargs):
        # Only observations are retried, never a POST or a partial download.
        for attempt in range(3):
            try:
                async with self.api.stream(
                    method, self.endpoint + path, **kwargs
                ) as res:
                    if method == "GET" and res.status_code in (429, 502, 503, 504):
                        if attempt < 2:
                            await asyncio.sleep(2**attempt)
                            continue
                    require(
                        res.status_code == 200, f"API returned HTTP {res.status_code}."
                    )
                    value = json.loads(await read_bounded(res, MAX_JSON))
                    require(isinstance(value, dict), "Malformed API response.")
                    return value
            except httpx.TransportError:
                if method != "GET" or attempt == 2:
                    raise ExampleError(
                        "API transport failed; submission was not replayed."
                    ) from None
                await asyncio.sleep(2**attempt)
        raise ExampleError("API observations exhausted their retry limit.")

    async def submit(self, input_path, endpoint_id):
        with input_path.open("rb") as source:
            data = source.read(MAX_INPUT + 1)
        require(0 < len(data) <= MAX_INPUT, "Input must contain at most 10 MiB.")
        receipt = {
            "version": 1,
            "endpoint": self.endpoint,
            "endpoint_id": endpoint_id,
            "image": IMAGE,
            "storage_host": self.storage_host,
            "bucket": self.bucket,
            "input_sha256": digest(data),
            "input_bytes": len(data),
            "options_sha256": digest(json.dumps(OPTIONS, sort_keys=True).encode()),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "state": "submission_unknown",
        }
        # A crash at any later point is conservatively an unknown submission.
        save_json(self.output / "receipt.json", receipt)
        task = await self.request_json(
            "POST",
            "/v1/convert/file/async",
            files={"files": (input_path.name, data, "application/octet-stream")},
            data=OPTIONS,
        )
        task_id = str(UUID(task["task_id"]))
        receipt.update(task_id=task_id, state="submitted")
        save_json(self.output / "receipt.json", receipt)
        return receipt

    async def collect(self, receipt, poll_interval=2):
        task_id = str(UUID(receipt["task_id"]))
        while True:
            task = await self.request_json("GET", f"/v1/status/poll/{task_id}?wait=0")
            require(task.get("task_id") == task_id, "Task identity mismatch.")
            state = task.get("task_status")
            require(
                state in ("pending", "started", "success", "failure"),
                "Unknown task state.",
            )
            if state == "failure":
                raise ExampleError("Conversion task failed; inspect operator logs.")
            if state == "success":
                break
            await asyncio.sleep(poll_interval)
        result = await self.request_json("GET", f"/v1/result/{task_id}")
        require(
            result.get("num_converted") == 1
            and result.get("num_succeeded") == 1
            and result.get("num_partially_succeeded", 0) == 0
            and result.get("num_failed") == 0,
            "Conversion was incomplete or partially successful.",
        )
        documents = result.get("documents")
        require(
            isinstance(documents, list) and len(documents) == 1,
            "Expected one document.",
        )
        document = documents[0]
        require(
            document.get("status") == "success" and not document.get("errors"),
            "Document conversion failed.",
        )
        artifacts = document.get("artifacts", [])
        require(
            len(artifacts) == 2
            and {a["artifact_type"] for a in artifacts} == {"markdown", "json"},
            "Expected exactly Markdown and JSON artifacts.",
        )
        # Validate every URL before downloading any artifact. Never persist signatures.
        identities = [
            artifact_identity(a["uri"], self.storage_host, self.bucket, task_id)
            for a in artifacts
        ]
        receipt["artifacts"] = [
            dict(identity, artifact_type=a["artifact_type"])
            for a, identity in zip(artifacts, identities)
        ]
        save_json(self.output / "receipt.json", receipt)
        manifest = []
        for artifact, identity in zip(artifacts, identities):
            kind = artifact["artifact_type"]
            filename = "document.md" if kind == "markdown" else "document.json"
            entry = await self.download(artifact["uri"], filename, kind)
            manifest.append(dict(identity, artifact_type=kind, **entry))
        complete = dict(receipt, state="complete", artifacts=manifest)
        save_json(self.output / "manifest.json", complete)
        return complete

    async def download(self, uri, filename, kind):
        temporary = self.output / (filename + ".part")
        size = 0
        checksum = hashlib.sha256()
        try:
            # This client has no Docling or ingress credentials and follows no redirects.
            async with self.storage.stream(
                "GET", uri, headers={"Accept-Encoding": "identity"}
            ) as res:
                require(
                    res.status_code == 200, f"Artifact returned HTTP {res.status_code}."
                )
                require(
                    res.headers.get("content-encoding", "identity") == "identity",
                    "Unexpected compressed artifact.",
                )
                length = res.headers.get("content-length")
                require(
                    length is None or 0 < int(length) <= MAX_ARTIFACT,
                    "Artifact size is outside the limit.",
                )
                with temporary.open("wb") as stream:
                    async for block in res.aiter_bytes():
                        size += len(block)
                        require(size <= MAX_ARTIFACT, "Artifact exceeds 20 MiB.")
                        stream.write(block)
                        checksum.update(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                require(
                    size > 0 and (length is None or size == int(length)),
                    "Artifact is empty or truncated.",
                )
            # Readback validates local bytes, not an independently supplied remote digest.
            content = temporary.read_bytes()
            require(
                digest(content) == checksum.hexdigest(),
                "Local readback checksum mismatch.",
            )
            if kind == "json":
                require(
                    json.loads(content).get("schema_name") == "DoclingDocument",
                    "Invalid Docling JSON artifact.",
                )
            else:
                require(
                    bool(content.decode("utf-8").strip()), "Empty Markdown artifact."
                )
            os.replace(temporary, self.output / filename)
            return {"file": filename, "bytes": size, "sha256": checksum.hexdigest()}
        finally:
            temporary.unlink(missing_ok=True)


def load_receipt(output, endpoint, endpoint_id, storage_host, bucket):
    receipt = json.loads((output / "receipt.json").read_text())
    require(
        receipt.get("version") == 1
        and receipt.get("endpoint") == endpoint
        and receipt.get("endpoint_id") == endpoint_id
        and receipt.get("storage_host") == storage_host
        and receipt.get("bucket") == bucket,
        "Receipt does not match the requested endpoint/storage configuration.",
    )
    require(
        receipt.get("state") == "submitted" and "task_id" in receipt,
        "Submission outcome unknown; reconcile manually. Do not resubmit automatically.",
    )
    return receipt


def verify_complete(output, receipt):
    manifest = json.loads((output / "manifest.json").read_text())
    require(
        manifest.get("task_id") == receipt["task_id"]
        and manifest.get("state") == "complete",
        "Invalid completion manifest.",
    )
    entries = manifest.get("artifacts", [])
    require(
        len(entries) == 2
        and {e["file"] for e in entries} == {"document.md", "document.json"},
        "Invalid manifest files.",
    )
    for entry in entries:
        data = (output / entry["file"]).read_bytes()
        require(
            len(data) == entry["bytes"] and digest(data) == entry["sha256"],
            "Saved artifact changed; completion cannot be verified.",
        )
    return manifest


async def run(args):
    endpoint = validate_endpoint(args.endpoint)
    require(
        re.fullmatch(r"[a-z0-9.-]+", args.storage_host) is not None,
        "Invalid storage host.",
    )
    require(
        re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", args.bucket) is not None,
        "Invalid bucket name.",
    )
    with locked_output(args.output, args.resume):
        receipt = None
        if args.resume:
            receipt = load_receipt(
                args.output, endpoint, args.endpoint_id, args.storage_host, args.bucket
            )
            if (args.output / "manifest.json").exists():
                return verify_complete(args.output, receipt)
        require(
            bool(os.environ.get("NEBIUS_ENDPOINT_TOKEN"))
            and bool(os.environ.get("DOCLING_API_KEY")),
            "Set NEBIUS_ENDPOINT_TOKEN and DOCLING_API_KEY.",
        )
        headers = {
            "Authorization": f"Bearer {os.environ['NEBIUS_ENDPOINT_TOKEN']}",
            "X-Api-Key": os.environ["DOCLING_API_KEY"],
        }
        async with (
            httpx.AsyncClient(
                headers=headers, timeout=30, follow_redirects=False, trust_env=False
            ) as api,
            httpx.AsyncClient(
                timeout=30, follow_redirects=False, trust_env=False
            ) as storage,
        ):
            client = ConversionClient(
                api, storage, endpoint, args.output, args.storage_host, args.bucket
            )
            if receipt is None:
                await client.request_json("GET", "/ready")
                receipt = await client.submit(args.file, args.endpoint_id)
            return await client.collect(receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--storage-host", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory on durable local storage; existing directory only with --resume.",
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Observe an acknowledged task; never submit again.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=900,
        help="Overall client deadline in seconds; does not cancel server work.",
    )
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    if (args.file is None) != args.resume:
        parser.error("Use --file for a new submission, or --resume without --file")
    try:
        asyncio.run(asyncio.wait_for(run(args), timeout=args.timeout))
    except ExampleError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except asyncio.TimeoutError:
        print(
            "Client deadline reached; server work was not cancelled. Use the receipt to reconcile.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, httpx.HTTPError):
        print(
            "Local I/O, transport or response validation failed. Preserve the receipt; do not blindly resubmit.",
            file=sys.stderr,
        )
        return 1
    print("Verified artifacts and manifest.json saved in the output directory.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
