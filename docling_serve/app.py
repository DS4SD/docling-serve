import base64
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Union

import httpx
from docling.datamodel.base_models import (
    ConversionStatus,
    DocumentStream,
    PipelineOptions,
)
from docling.datamodel.document import ConversionResult, DocumentConversionInput
from docling.document_converter import DocumentConverter
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from docling_serve.settings import Settings


class HttpSource(BaseModel):
    url: str
    headers: Dict[str, Any] = {}


class FileSource(BaseModel):
    base64_string: str
    filename: str


class ConvertDocumentHttpSourceRequest(BaseModel):
    http_source: HttpSource


class ConvertDocumentFileSourceRequest(BaseModel):
    file_source: FileSource


class ConvertDocumentResponse(BaseModel):
    content_md: str


ConvertDocumentRequest = Union[
    ConvertDocumentFileSourceRequest, ConvertDocumentHttpSourceRequest
]


models = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Converter
    settings = Settings()
    pipeline_options = PipelineOptions()
    pipeline_options.do_ocr = settings.do_ocr
    pipeline_options.do_table_structure = settings.do_table_structure
    models["converter"] = DocumentConverter(pipeline_options=pipeline_options)
    yield

    models.clear()


app = FastAPI(
    title="Docling Serve",
    lifespan=lifespan,
)


@app.post("/convert")
def convert_pdf_document(
    body: ConvertDocumentRequest,
) -> ConvertDocumentResponse:

    filename: str
    buf: BytesIO

    if isinstance(body, ConvertDocumentFileSourceRequest):
        buf = BytesIO(base64.b64decode(body.file_source.base64_string))
        filename = body.file_source.filename
    elif isinstance(body, ConvertDocumentHttpSourceRequest):
        http_res = httpx.get(body.http_source.url, headers=body.http_source.headers)
        buf = BytesIO(http_res.content)
        filename = Path(
            body.http_source.url
        ).name  # TODO: use better way to detect filename, e.g. from Content-Disposition

    docs_input = DocumentConversionInput.from_streams(
        [DocumentStream(filename=filename, stream=buf)]
    )
    result: ConversionResult = next(models["converter"].convert(docs_input), None)

    if result is None or result.status != ConversionStatus.SUCCESS:
        raise HTTPException(status_code=500, detail={"errors": result.errors})

    return ConvertDocumentResponse(content_md=result.render_as_markdown())
