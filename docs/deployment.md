# Deployment Examples

This document provides deployment examples for running the application in different environments.

Choose the deployment option that best fits your setup.

- **[Local GPU NVIDIA](#local-gpu-nvidia)**: For deploying the application locally on a machine with a supported NVIDIA GPU (using Docker Compose).
- **[Local GPU AMD](#local-gpu-amd)**: For deploying the application locally on a machine with a supported AMD GPU (using Docker Compose).
- **[Nebius Serverless](#nebius-serverless)**: A single-GPU Endpoint with asynchronous conversion and Object Storage results.
- **[OpenShift](#openshift)**: For deploying the application on an OpenShift cluster, designed for cloud-native environments.

---

## Local GPU NVIDIA

### Docker compose

Manifest example: [compose-nvidia.yaml](./deploy-examples/compose-nvidia.yaml)

This deployment has the following features:

- NVIDIA cuda enabled

Install the app with:

```sh
docker compose -f docs/deploy-examples/compose-nvidia.yaml up -d
```

For using the API:

```sh
# Make a test query
curl -X 'POST' \
  "localhost:5001/v1/convert/source/async" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "sources": [{"kind": "http", "url": "https://arxiv.org/pdf/2501.17887"}]
  }'
```

<details>
<summary><b>Requirements</b></summary>

- debian/ubuntu/rhel/fedora/opensuse
- docker
- nvidia drivers >=550.54.14
- nvidia-container-toolkit

Docs:

- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/supported-platforms.html)
- [CUDA Toolkit Release Notes](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html#id6)

</details>

<details>
<summary><b>Steps</b></summary>

1. Check driver version and which GPU you want to use 0/1/2/n (and update [compose-nvidia.yaml](./deploy-examples/compose-nvidia.yaml) file or use `count: all`)

    ```sh
    nvidia-smi
    ```

2. Check if the NVIDIA Container Toolkit is installed/updated

    ```sh
    # debian
    dpkg -l | grep nvidia-container-toolkit
    ```

    ```sh
    # rhel
    rpm -q nvidia-container-toolkit
    ```

    NVIDIA Container Toolkit install steps can be found here:

    <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>

3. Check which runtime is being used by Docker

    ```sh
    # docker
    docker info | grep -i runtime
    ```

4. If the default Docker runtime changes back from 'nvidia' to 'default' after restarting the Docker service (optional):

    Backup the daemon.json file:

    ```sh
    sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak
    ```

    Update the daemon.json file:

    ```sh
    echo '{
      "runtimes": {
        "nvidia": {
          "path": "nvidia-container-runtime"
        }
      },
      "default-runtime": "nvidia"
    }' | sudo tee /etc/docker/daemon.json > /dev/null
    ```

    Restart the Docker service:

    ```sh
    sudo systemctl restart docker
    ```

    Confirm 'nvidia' is the default runtime used by Docker by repeating step 3.

5. Run the container:

    ```sh
    docker compose -f docs/deploy-examples/compose-nvidia.yaml up -d
    ```

</details>

## Local GPU AMD

### Docker compose

Manifest example: [compose-amd.yaml](./deploy-examples/compose-amd.yaml)

This deployment has the following features:

- AMD rocm enabled

Install the app with:

```sh
docker compose -f docs/deploy-examples/compose-amd.yaml up -d
```

For using the API:

```sh
# Make a test query
curl -X 'POST' \
  "localhost:5001/v1/convert/source/async" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "sources": [{"kind": "http", "url": "https://arxiv.org/pdf/2501.17887"}]
  }'
```

<details>
<summary><b>Requirements</b></summary>

- debian/ubuntu/rhel/fedora/opensuse
- docker
- AMDGPU driver >=6.3
- AMD ROCm >=6.3

Docs:

- [AMD ROCm installation](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html)

</details>

<details>
<summary><b>Steps</b></summary>

1. Check driver version and which GPU you want to use 0/1/2/n (and update [compose-amd.yaml](./deploy-examples/compose-amd.yaml) file)

    ```sh
    rocm-smi --showdriverversion
    rocminfo | grep -i "ROCm version"
    ```

2. Find both video group GID and render group GID from host (and update [compose-amd.yaml](./deploy-examples/compose-amd.yaml) file)

    ```sh
    getent group video
    getent group render
    ```

3. Build the image locally (and update [compose-amd.yaml](./deploy-examples/compose-amd.yaml) file)

    ```sh
    make docling-serve-rocm-image
    ```

</details>

## Nebius Serverless

See the [Nebius Serverless Endpoint guide](./deploy-examples/nebius-serverless/README.md)
and [configuration example](./deploy-examples/nebius-serverless/docling-config.yaml).
The example serves a trusted client with one local worker; exported objects persist
independently, while task status is lost if the server process is replaced.
The guide records a validated L40S configuration and the limits of its live checks.

## OpenShift

### Simple deployment

Manifest example: [docling-serve-simple.yaml](./deploy-examples/docling-serve-simple.yaml)

This deployment example has the following features:

- Deployment configuration
- Service configuration
- NVIDIA cuda enabled

Install the app with:

```sh
oc apply -f docs/deploy-examples/docling-serve-simple.yaml
```

For using the API:

```sh
# Port-forward the service
oc port-forward svc/docling-serve 5001:5001

# Make a test query
curl -X 'POST' \
  "localhost:5001/v1/convert/source/async" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "sources": [{"kind": "http", "url": "https://arxiv.org/pdf/2501.17887"}]
  }'
```

### Multiple workers with RQ

Manifest example: [`docling-serve-rq-workers.yaml`](./deploy-examples/docling-serve-rq-workers.yaml)

This deployment example has the following features:

- Deployment configuration
- Service configuration
- Redis deployment
- Multiple (2 by default) worker Pods

Install the app with:

- create k8s secret:

```sh
kubectl create secret generic docling-serve-rq-secrets --from-literal=REDIS_PASSWORD=myredispassword --from-literal=RQ_REDIS_URL=redis://:myredispassword@docling-serve-redis-service:6373/
```

- apply deployment manifest:

```sh
oc apply -f docs/deploy-examples/docling-serve-rq-workers.yaml
```

### Secure deployment with `oauth-proxy`

Manifest example: [docling-serve-oauth.yaml](./deploy-examples/docling-serve-oauth.yaml)

This deployment has the following features:

- TLS encryption between all components (using the cluster-internal CA authority).
- Authentication via a secure `oauth-proxy` sidecar.
- Expose the service using a secure OpenShift `Route`

Install the app with:

```sh
oc apply -f docs/deploy-examples/docling-serve-oauth.yaml
```

For using the API:

```sh
# Retrieve the endpoint
DOCLING_NAME=docling-serve
DOCLING_ROUTE="https://$(oc get routes ${DOCLING_NAME} --template={{.spec.host}})"

# Retrieve the authentication token
OCP_AUTH_TOKEN=$(oc whoami --show-token)

# Make a test query
curl -X 'POST' \
  "${DOCLING_ROUTE}/v1/convert/source/async" \
  -H "Authorization: Bearer ${OCP_AUTH_TOKEN}" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "sources": [{"kind": "http", "url": "https://arxiv.org/pdf/2501.17887"}]
  }'
```

### ReplicaSets with `sticky sessions`

Manifest example: [docling-serve-replicas-w-sticky-sessions.yaml](./deploy-examples/docling-serve-replicas-w-sticky-sessions.yaml)

This deployment has the following features:

- Deployment configuration with 3 replicas
- Service configuration
- Expose the service using a OpenShift `Route` and enables sticky sessions

Install the app with:

```sh
oc apply -f docs/deploy-examples/docling-serve-replicas-w-sticky-sessions.yaml
```

For using the API:

```sh
# Retrieve the endpoint
DOCLING_NAME=docling-serve
DOCLING_ROUTE="https://$(oc get routes $DOCLING_NAME --template={{.spec.host}})"

# Make a test query, store the cookie and taskid
task_id=$(curl -s -X 'POST' \
    "${DOCLING_ROUTE}/v1/convert/source/async" \
    -H "accept: application/json" \
    -H "Content-Type: application/json" \
    -d '{
      "sources": [{"kind": "http", "url": "https://arxiv.org/pdf/2501.17887"}]
    }' \
    -c cookies.txt | grep -oP '"task_id":"\K[^"]+')
```

```sh
# Grab the taskid and cookie to check the task status
curl -v -X 'GET' \
  "${DOCLING_ROUTE}/v1/status/poll/$task_id?wait=0" \
  -H "accept: application/json" \
  -b "cookies.txt"
```
