# Docling Serve

 Running [Docling](https://github.com/DS4SD/docling) as an API service.

 > [!NOTE]
> This is an unstable draft implementation which will quickly evolve.

## Development

Install the dependencies

```sh
# Install poetry if not already available
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Run the server
poetry run uvicorn docling_serve.app:app --reload
```

Example payload (http source):

```sh
curl -X 'POST' \
  'http://127.0.0.1:8000/convert' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "http_source": {
    "url": "https://arxiv.org/pdf/2206.01062"
  }
}'
```

### Cuda GPU Support

For GPU support try the following:

```sh
# Create a virtual env
python3 -m venv venv

# Activate the venv
source venv/bin/active

# Install torch with the special index
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Install the package
pip install -e .

# Run the server
poetry run uvicorn docling_serve.app:app --reload
```