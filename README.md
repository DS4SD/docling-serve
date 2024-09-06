# Docling Serve

 Running [Docling](https://github.com/DS4SD/docling) as an API service.

 > [!NOTE]
> This is an unstable draft implementation which will quickly evolve.


## Development

Install the dependencies

```sh
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
