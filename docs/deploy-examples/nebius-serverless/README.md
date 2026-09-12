# Nebius Serverless Endpoint

Run one Docling Serve GPU container on a [Nebius Serverless Endpoint](https://docs.nebius.com/serverless/overview), submit a document asynchronously, and export Markdown and Docling JSON to a private Object Storage bucket. Nebius manages the container's compute lifecycle; the application uses the existing Docling API.

This example is for a trusted client with one conversion in flight. Its local engine keeps task status in memory. Exported objects can outlive the Endpoint, but accepted tasks cannot resume after the server process is replaced. There is no automatic scale-to-zero or durable queue in this example.

> [!NOTE]
> Validated on September 11, 2026 with the pinned image, Nebius CLI 0.12.265 and one L40S (`gpu-l40s-a`, `1gpu-16vcpu-64gb`) in `eu-north1`: managed ingress, native SecretStash/S3, text/table and scanned PDFs, independent artifact readback, authentication, input limits, export failure, client resume, stop/start and resource cleanup. These are small fixture checks, not a throughput or production SLA guarantee.

## Prerequisites

- A Nebius project with Serverless permissions, available GPU quota and a subnet in the selected region. Use a regular, single-GPU platform/preset pair available in that project.
- [Nebius CLI](https://docs.nebius.com/cli/install) with a separately configured operator identity. Command syntax below was inspected with CLI `0.12.265`.
- A private result bucket in the selected region. Configure retention for `docling-example/`, old object versions where enabled, and incomplete multipart uploads. Bucket storage remains separately billed after the Endpoint stops.
- [SecretStash](https://docs.nebius.com/mysterybox/overview) secret versions for the keys below. Grant the runtime only access needed to read its secrets and write/read its result objects; do not give the container Endpoint lifecycle credentials.
- Python 3.10+ on Linux/macOS, with `httpx==0.28.1` for the example client. Store its output directory on durable local storage. The client uses POSIX file locks to prevent two writers using the same receipt.

| Secret selector variable | Payload keys |
| --- | --- |
| `INGRESS_SECRET_SELECTOR` | `AUTH_TOKEN` |
| `APP_SECRET_SELECTOR` | `DOCLING_SERVE_API_KEY` |
| `STORAGE_SECRET_SELECTOR` | `DOCLING_SERVE_ARTIFACT_STORAGE_ACCESS_KEY`, `DOCLING_SERVE_ARTIFACT_STORAGE_SECRET_KEY` |

Use secret IDs/version selectors from your project, not literal credentials in the configuration file. The storage secret contains S3 credentials, not a Nebius IAM bearer token. The ingress and application authentication tokens should be different. Do not put any of these values in Git or an image, and avoid verbose HTTP tracing that records request headers or signed URLs.

## Image and configuration

This example uses Docling Serve `v1.32.0`, its locked Docling `2.124.0` and Jobkit `3.5.0`, and the published CUDA 12.8 image. The Linux amd64 manifest was inspected on September 11, 2026:

```bash
export PINNED_IMAGE='ghcr.io/docling-project/docling-serve-cu128@sha256:c57b384ba305ab70f4f8cd02582a0aa9effbc529b7838ddc324a5d299449e8d2'
```

Keep this value aligned with `IMAGE` in [client.py](client.py), where it is recorded as operator-supplied deployment provenance; the client cannot attest the remotely running image. Revalidate the image after an upgrade. Use an amd64 GPU preset and check driver compatibility, actual Torch CUDA availability and selected OCR engine before claiming GPU acceleration.

The image already contains the default model artifacts. Keep its model directory intact; do not mount an empty directory over it or download models on every request. Extra VLM/code/formula models require separate preparation as described in [model handling](../../models.md). Default startup warm-up uses auto OCR; the first explicit EasyOCR request initializes another pipeline. GPU allocation does not guarantee every OCR stage uses the GPU; the client explicitly requests EasyOCR with `ocr_preset=easyocr`. The deprecated multipart `ocr_engine` field did not override the default `auto` preset in the tested version. Validate its GPU execution separately from layout/table inference.

[docling-config.yaml](docling-config.yaml) selects one Uvicorn process (set below), one local converter worker, shared models and model warm-up. The example policy allows one source, 20 pages and 10 MiB per document, with a 300-second document timeout. It restricts outputs to the server-managed `presigned_url` target. These are starting limits, not measured capacity.

Scratch stays ephemeral. **Do not mount result storage at `DOCLING_SERVE_SCRATCH_PATH`:** the current server shutdown path deletes an explicitly configured scratch directory. Result objects are uploaded through the S3 connector instead. No bucket filesystem mount is required.

## Create the Endpoint

Work from this directory so the injected config path resolves. Set the following non-secret values for your project:

```bash
export PROJECT_ID='<project-id>'
export SUBNET_ID='<subnet-id>'
export ENDPOINT_NAME='docling-example'
export GPU_PLATFORM='<available-single-gpu-platform>'
export GPU_PRESET='<matching-single-gpu-preset>'
export REGION='<bucket-and-endpoint-region>'
export S3_HOST="storage.${REGION}.nebius.cloud"
export RESULT_BUCKET='<private-result-bucket>'
export INGRESS_SECRET_SELECTOR='<ingress-secret-id@version-id>'
export APP_SECRET_SELECTOR='<app-secret-id@version-id>'
export STORAGE_SECRET_SELECTOR='<storage-secret-id@version-id>'
```

`S3_HOST` has **no protocol prefix**; the Docling S3 connector adds HTTPS. Verify the signing region and bucket addressing against the selected Nebius region. The explicit 250 GiB disk and 16 GiB shared memory below are initial allocations, not minimum requirements. Set a test budget and an independent operator stop deadline before launching; an idle Endpoint continues consuming allocated compute.

```bash
umask 077
nebius ai endpoint create \
  --parent-id "$PROJECT_ID" --subnet-id "$SUBNET_ID" \
  --name "$ENDPOINT_NAME" --image "$PINNED_IMAGE" \
  --platform "$GPU_PLATFORM" --preset "$GPU_PRESET" \
  --disk-size 250Gi --shm-size 16Gi \
  --container-port 5001 \
  --auth token --token-secret "$INGRESS_SECRET_SELECTOR" \
  --inject-file 'docling-config.yaml:/etc/docling/config.yaml' \
  --env DOCLING_SERVE_CONFIG_FILE=/etc/docling/config.yaml \
  --env UVICORN_HOST=0.0.0.0 --env UVICORN_PORT=5001 --env UVICORN_WORKERS=1 \
  --env DOCLING_DEVICE=cuda:0 \
  --env "DOCLING_SERVE_ARTIFACT_STORAGE_ENDPOINT=$S3_HOST" \
  --env "DOCLING_SERVE_ARTIFACT_STORAGE_BUCKET=$RESULT_BUCKET" \
  --env "AWS_DEFAULT_REGION=$REGION" \
  --env-secret "DOCLING_SERVE_API_KEY=$APP_SECRET_SELECTOR" \
  --env-secret "DOCLING_SERVE_ARTIFACT_STORAGE_ACCESS_KEY=$STORAGE_SECRET_SELECTOR" \
  --env-secret "DOCLING_SERVE_ARTIFACT_STORAGE_SECRET_KEY=$STORAGE_SECRET_SELECTOR" \
  --async --format json > create-receipt.txt
```

The image's default `docling-serve run` command listens on port 5001. A managed HTTPS URL needs neither `--public` nor an SSH key. Injected files are read-only and limited to 64 KiB. See [Endpoint management](https://docs.nebius.com/serverless/endpoints/manage) for current flags and IAM prerequisites.

CLI `0.12.265` prints `Endpoint ID: <id>` for this create command, even with `--format json`; it does not return a JSON operation receipt. Save the text response and use that Endpoint ID to observe provisioning. If create times out or its response is lost, reconcile the resource in the console or with `get-by-name` before retrying. A name alone does not prove resource identity. Do not run create repeatedly while waiting for models or capacity.

```bash
export ENDPOINT_ID='<id-from-create-receipt>'
nebius ai endpoint get --id "$ENDPOINT_ID" --format json
```

Observe the Endpoint state until startup succeeds or fails. From the Endpoint's `status.public_endpoints`, copy the HTTPS origin for port 5001 into `ENDPOINT_URL`. Do not construct a hostname or append `/v1`. Provider `RUNNING` alone does not mean the models are loaded: check `/ready` before submission. The client checks it too. A client timeout ends observation, not remote provisioning; continue inspecting the same Endpoint ID. The [lifecycle documentation](https://docs.nebius.com/serverless/lifecycle) describes capacity and failure states.

## Convert and collect results

Load `NEBIUS_ENDPOINT_TOKEN` and `DOCLING_API_KEY` securely into the client's environment from the matching ingress and application secrets. These are runtime request tokens, not the operator's IAM credential. The client sends both `Authorization: Bearer ...` and `X-Api-Key` to Docling, and sends neither to storage.

```bash
python -m venv .client-venv
.client-venv/bin/python -m pip install 'httpx==0.28.1'
export ENDPOINT_URL='<managed-https-origin>'
.client-venv/bin/python client.py \
  --endpoint "$ENDPOINT_URL" --endpoint-id "$ENDPOINT_ID" \
  --storage-host "$S3_HOST" --bucket "$RESULT_BUCKET" \
  --file ./document.pdf --output ./conversion-001 --timeout 900
```

Submit one small PDF first. The client also accepts local formats supported by Docling. It snapshots at most 10 MiB of input, sends one multipart async request, polls status, and retrieves the result. The requested output formats are Markdown and JSON with placeholder images. It requires one fully successful document and both artifacts; a partial conversion fails the example.

The output directory must not already exist for a new submission. The client writes:

- `receipt.json` before submitting, initially with `submission_unknown`; after acknowledgment, it records `task_id` and `submitted`. It contains input/options hashes, Endpoint identity, image provenance and unsigned bucket/key references when available.
- `document.md` and `document.json`, each bounded to 20 MiB. Remote filenames never control local paths. Downloads require HTTPS and the configured storage host/bucket/task prefix; redirects are rejected.
- `manifest.json` only after both downloads pass structure checks and local checksum readback. It contains stable bucket/key references, sizes and SHA-256 values, with no signed URLs or tokens. Preserve it in application-owned durable storage.

These checks establish the downloaded bytes and basic output shape. They do not prove OCR quality or compare against an independently supplied remote checksum. For production acceptance, independently read the objects using a separate storage identity and compare content/expected fixture text.

### Recover client observation

If the client stops after recording a task ID, observe that task again with the same configuration and output directory:

```bash
.client-venv/bin/python client.py \
  --endpoint "$ENDPOINT_URL" --endpoint-id "$ENDPOINT_ID" \
  --storage-host "$S3_HOST" --bucket "$RESULT_BUCKET" \
  --output ./conversion-001 --resume --timeout 900
```

Resume never sends a new conversion POST. If a completion manifest already exists, it verifies the saved files locally. If the receipt says `submission_unknown`, stop and reconcile manually: Docling offers no demonstrated idempotent submission contract here. A crash before dispatch is also conservatively unknown. A failure before a receipt exists means this client did not dispatch the conversion.

GET observations have at most three attempts for transient transport errors or HTTP 429/502/503/504. The overall deadline bounds client work; it does not cancel conversion or stop the Endpoint. Downloads are not automatically retried midstream. An interrupted download can be attempted again with resume while the task and signed URLs remain available.

The example retains fetched task results for one hour and issues one-hour artifact URLs. Fetching again does not promise URL renewal. A 404 may indicate process replacement or result cleanup; it does not authorize resubmission. Preserve receipts and inspect the result prefix. Expired URLs do not delete objects: use stable bucket/key references with an authorized storage client. Avoid logging returned result bodies because they contain signed URLs.

## Operating limits

- Authentication is for a shared trusted client, not individual users or tenant isolation. A task UUID is not an authorization boundary. HTTP sources and callbacks can cause outbound requests; disabling remote model services does not disable all egress.
- One worker does not limit pending submissions: the local queue is unbounded. Use one in-flight request and add admission controls before opening the service to more clients. File/page limits apply to document processing; multipart input can enter memory before those checks.
- The 300-second document timeout is not a queue-wait or hard GPU termination guarantee. Synchronous HTTP wait and Uvicorn keep-alive settings are different limits.
- Output size limits in the client do not cap server generation/upload. Keep enrichment and embedded image exports out of this example. Managed ingress request/response limits and drain/restart behavior need separate validation.
- Container/process replacement loses local task status. The `presigned_url` target persists files, not the queue. Use an independently validated RQ deployment for a recoverable service, or finite Serverless Jobs for batch workloads.
- Partial uploads can remain after a failed task. A completed object is not evidence the whole conversion succeeded. Treat the completion manifest as the application publication marker, and configure cleanup of abandoned prefixes/multipart uploads.

## Stop, restart and cleanup

Stop admitting work, drain known tasks and save their verified artifacts before maintenance. The client never starts, stops or deletes the Endpoint.

```bash
nebius ai endpoint stop --id "$ENDPOINT_ID" --async --format json
# CLI 0.12.265 prints a bare operation ID, even with --format json.
# Save that ID, then wait for and inspect the operation.
nebius ai endpoint operation wait --id '<stop-operation-id>' --timeout 35m
nebius ai endpoint get --id "$ENDPOINT_ID" --format json

nebius ai endpoint start --id "$ENDPOINT_ID" --async --format json
# Save the bare start operation ID, wait for it, then recheck /ready.
```

Completed deletion can also remove the operation record; operation `NotFound` alone is not cleanup proof. Confirm the Endpoint and recorded VM/disk IDs are gone. Do not equate a status transition with completed cleanup. Expect old task IDs to disappear when the process is replaced, while bucket objects remain. When the Endpoint is no longer needed:

```bash
nebius ai endpoint delete --id "$ENDPOINT_ID" --async --format json
nebius ai endpoint operation wait --id '<delete-operation-id>' --timeout 35m
nebius ai endpoint get --id "$ENDPOINT_ID" --format json
# Confirm NotFound; if the operation failed or resources linger, investigate it.
```

Nebius documents deletion of the managed VM and boot disk with the Endpoint. Before deletion, record each `status.instances[].compute_instance_id` and read its `spec.boot_disk.existing_disk.id`. Managed resources can belong to an `appbox` container and be absent from project-level Compute lists. After deletion, query the recorded IDs directly; record/support-investigate anything still present. Delete only test-owned objects, versions, incomplete uploads and secret versions when they are no longer needed. Endpoint deletion does not delete the result bucket or revoke its credentials. Never recursively delete a shared bucket as part of this example.

To clear old *finished* tasks while keeping the server alive, the existing authenticated API is `GET /v1/clear/results?older_then=3600` (the parameter spelling is intentional). It clears task results, not Object Storage objects. Abandoned tasks/results and bucket retention need operator management even when single-use results are enabled.

## Validate an installation

Before describing a configuration as supported, record the image/model versions and GPU/OCR provider, verify missing/wrong-token rejection, convert a text/table and scanned PDF, exercise input/output limits, and independently read back artifacts. Test client disconnect/resume, upload failure, stop/start, URL expiry and cleanup. Confirm that process loss is reported as task loss rather than successful recovery. Repeat these GPU/provider checks within a separately bounded budget when changing the image, region or hardware. The validation above confirmed application URLs declare a one-hour TTL and separately checked native S3 expiry with a two-second URL; it did not await the application URL’s full hour or measure all ingress/output limits.

The client regression tests run without cloud credentials:

```bash
# From the repository root, using the project's development environment:
uv run --no-sync pytest tests/test_nebius_serverless_client.py
```
