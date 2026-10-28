"""Knowledge documents of one of the caller's business profiles (FR-05, FR-09-03; UC-05).

`POST knowledge/ask` runs the Knowledge agent alone, so operators can test their documents
without a whole conversation (`sessions.py`).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Response, UploadFile, status

from app.agents.contracts import AgentContext, AgentResult
from app.agents.knowledge.agent import KnowledgeAgent, KnowledgeSubtask
from app.api.v1.profiles import CurrentProfile
from app.auth.dependencies import CurrentAuth
from app.core.config import Settings, get_settings
from app.knowledge.api_schemas import DocumentOut, DocumentTextOut, UploadOut
from app.knowledge.dependencies import get_knowledge_service, get_llm
from app.knowledge.models import KnowledgeDocument
from app.knowledge.service import KnowledgeService, Upload
from app.providers.llm import LLMClient

router = APIRouter(prefix="/profiles/{profile_id}", tags=["knowledge"])

Service = Annotated[KnowledgeService, Depends(get_knowledge_service)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or document in your business"}
}
_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_413_CONTENT_TOO_LARGE: {"description": "The file is over MAX_DOCUMENT_BYTES"},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Not a supported file type (.pdf, .docx, .txt, .md) or no text in it"
    },
}


@router.post(
    "/documents",
    response_model=UploadOut,
    responses={**_NOT_FOUND, **_UPLOAD_ERRORS, status.HTTP_200_OK: {"model": UploadOut}},
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    profile: CurrentProfile,
    auth: CurrentAuth,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
    file: Annotated[UploadFile, File(description="A .pdf, .docx, .txt or .md file")],
) -> UploadOut:
    # Read one byte past the limit, so an oversized file is refused without reading it all.
    data = await file.read(settings.max_document_bytes + 1)
    result = await service.upload(
        profile,
        Upload(
            filename=file.filename or "",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        ),
        uploaded_by=auth.operator_id,
    )
    if result.replaced:
        response.status_code = status.HTTP_200_OK
    return UploadOut.model_validate(
        {**DocumentOut.model_validate(result.document).model_dump(), "replaced": result.replaced}
    )


@router.get("/documents", response_model=list[DocumentOut], responses=_NOT_FOUND)
async def list_documents(profile: CurrentProfile, service: Service) -> list[KnowledgeDocument]:
    return await service.list_documents(profile)


@router.get("/documents/{document_id}", response_model=DocumentTextOut, responses=_NOT_FOUND)
async def get_document(
    profile: CurrentProfile, document_id: uuid.UUID, service: Service
) -> KnowledgeDocument:
    return await service.get(profile, document_id)


@router.delete(
    "/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND
)
async def delete_document(
    profile: CurrentProfile, document_id: uuid.UUID, service: Service
) -> None:
    await service.delete(profile, document_id)


@router.post("/knowledge/ask", response_model=AgentResult, responses=_NOT_FOUND)
async def ask(
    profile: CurrentProfile,
    auth: CurrentAuth,
    body: KnowledgeSubtask,
    service: Service,
    llm: Annotated[LLMClient | None, Depends(get_llm)],
) -> AgentResult:
    agent = KnowledgeAgent(service, llm)
    return await agent.run(AgentContext(tenant_id=auth.tenant_id, profile=profile), body)
