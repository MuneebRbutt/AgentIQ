"""Knowledge documents and the Knowledge agent over the API (FR-05, FR-09-03, TC-05).

Runs with the offline hashing embedder, an in-memory Chroma and no real model (see the
integration settings); a fake model is plugged in where the LLM path is tested.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import SystemClock
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.knowledge.dependencies import get_llm
from app.knowledge.service import KnowledgeService, Upload, clean_filename
from app.knowledge.store import ChromaVectorStore
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.profiles.service import ProfileService
from app.providers.llm import LLMClient, LLMError
from tests.fakes import FakeLLM
from tests.integration.conftest import DEFAULT_PASSWORD, OperatorFactory

pytestmark = pytest.mark.integration

POLICIES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "hotel_policies.md"
).read_bytes()


@dataclass
class Operator:
    client: httpx.AsyncClient
    headers: dict[str, str]
    profile: BusinessProfile
    base: str = field(init=False)

    def __post_init__(self) -> None:
        self.base = f"/api/v1/profiles/{self.profile.id}"

    async def upload(
        self,
        data: bytes = POLICIES,
        filename: str = "policies.md",
        content_type: str = "text/markdown",
    ) -> httpx.Response:
        return await self.client.post(
            f"{self.base}/documents",
            files={"file": (filename, data, content_type)},
            headers=self.headers,
        )

    async def ask(self, question: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base}/knowledge/ask", json={"question": question}, headers=self.headers
        )
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    async def get(self, path: str) -> httpx.Response:
        return await self.client.get(f"{self.base}{path}", headers=self.headers)


async def _operator(
    client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
    email: str = "owner@grand.example.com",
    *,
    profile_facts: bool = False,
) -> Operator:
    """An operator of a hotel. Its profile's own policies and FAQs are left out unless
    `profile_facts`, so answers in the document tests can only come from documents."""
    tenant = await factory.tenant()
    await factory.operator(email, tenant=tenant)
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    config = load_example(Domain.HOTEL)
    if not profile_facts:
        config = config.model_copy(update={"policies": [], "faqs": [], "description": None})
    profile = await ProfileService(db, factory.clock).create(tenant.id, config)
    return Operator(client, headers, profile)


@pytest.fixture
async def hotel(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> Operator:
    return await _operator(live_client, factory, db)


def _use_llm(app: FastAPI, llm: LLMClient) -> None:
    app.dependency_overrides[get_llm] = lambda: llm


async def test_tc05_upload_then_answer_from_the_document(hotel: Operator) -> None:
    response = await hotel.upload()
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["filename"] == "policies.md"
    assert document["replaced"] is False
    assert document["chunk_count"] >= 2
    assert document["size_bytes"] == len(POLICIES)

    result = await hotel.ask("What is your cancellation policy?")
    assert result["status"] == "completed"
    assert "cancelled free of charge up to 24 hours" in result["message"]
    assert result["data"]["mode"] == "extractive"  # no model configured in tests
    assert result["data"]["sources"][0]["filename"] == "policies.md"


async def test_tc05_out_of_scope_question_gets_insufficient_information(
    hotel: Operator,
) -> None:
    await hotel.upload()
    result = await hotel.ask("Do you offer valet parking?")
    assert result["status"] == "no_answer"
    assert "doesn't cover" in result["message"]


async def test_no_documents_means_no_answer(hotel: Operator) -> None:
    assert (await hotel.ask("What time is breakfast?"))["status"] == "no_answer"


async def test_answer_is_written_by_the_model_when_configured(
    hotel: Operator, live_app: FastAPI
) -> None:
    llm = FakeLLM("You can cancel for free until 24 hours before check-in.")
    _use_llm(live_app, llm)
    await hotel.upload()

    result = await hotel.ask("Can I cancel my reservation?")
    assert result["status"] == "completed"
    assert result["message"] == "You can cancel for free until 24 hours before check-in."
    assert result["data"]["mode"] == "llm"
    assert "cancelled free of charge" in llm.calls[0][1]["content"]


async def test_model_declining_or_failing(hotel: Operator, live_app: FastAPI) -> None:
    await hotel.upload()
    _use_llm(live_app, FakeLLM("INSUFFICIENT_INFORMATION"))
    assert (await hotel.ask("Can I cancel my reservation?"))["status"] == "no_answer"

    _use_llm(live_app, FakeLLM(LLMError("timeout")))
    result = await hotel.ask("Can I cancel my reservation?")
    assert result["status"] == "completed"
    assert result["data"]["mode"] == "extractive"  # the customer still gets an answer


async def test_reupload_with_the_same_name_replaces_the_document(
    hotel: Operator, live_app: FastAPI
) -> None:
    first = (await hotel.upload()).json()
    response = await hotel.upload(b"Valet parking costs PKR 500 per night.", "policies.md")
    assert response.status_code == 200
    second = response.json()
    assert second["replaced"] is True
    assert second["created_at"] == first["created_at"]

    listed = (await hotel.get("/documents")).json()
    assert [doc["id"] for doc in listed] == [second["id"]]
    # The old text is gone from search; the new text answers (FR-05-06).
    assert (await hotel.ask("What is your cancellation policy?"))["status"] == "no_answer"
    assert "PKR 500" in (await hotel.ask("How much is valet parking?"))["message"]
    store: ChromaVectorStore = live_app.state.vector_store
    assert await store.count(hotel.profile.tenant_id) == second["chunk_count"]


async def test_get_list_and_delete(hotel: Operator, live_app: FastAPI) -> None:
    doc = (await hotel.upload()).json()
    await hotel.upload(b"Parking is free for guests.", "parking.txt", "text/plain")

    listed = (await hotel.get("/documents")).json()
    assert [d["filename"] for d in listed] == ["parking.txt", "policies.md"]
    detail = (await hotel.get(f"/documents/{doc['id']}")).json()
    assert "Cancellation policy" in detail["text"]

    response = await hotel.client.delete(
        f"{hotel.base}/documents/{doc['id']}", headers=hotel.headers
    )
    assert response.status_code == 204
    assert (await hotel.get(f"/documents/{doc['id']}")).status_code == 404
    assert (await hotel.ask("What is your cancellation policy?"))["status"] == "no_answer"
    assert (await hotel.ask("Is parking free?"))["status"] == "completed"


async def test_deleting_the_profile_removes_its_passages(
    hotel: Operator, live_app: FastAPI
) -> None:
    await hotel.upload()
    response = await hotel.client.delete(hotel.base, headers=hotel.headers)
    assert response.status_code == 204
    store: ChromaVectorStore = live_app.state.vector_store
    assert await store.count(hotel.profile.tenant_id) == 0


async def test_unsupported_and_empty_files_are_rejected(hotel: Operator) -> None:
    response = await hotel.upload(b"\x89PNG\r\n", "menu.png", "image/png")
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_document"
    assert (await hotel.upload(b"   ", "blank.txt")).status_code == 422
    assert (await hotel.get("/documents")).json() == []


async def test_oversized_file_is_rejected(
    hotel: Operator, live_app: FastAPI, integration_settings: Settings
) -> None:
    small = integration_settings.model_copy(update={"max_document_bytes": 1024})
    live_app.dependency_overrides[get_settings] = lambda: small
    response = await hotel.upload(b"a " * 600, "big.txt")
    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


async def test_folder_part_of_the_filename_is_dropped(hotel: Operator) -> None:
    response = await hotel.upload(b"Breakfast at 7.", "C:\\Users\\sara\\faq.txt", "text/plain")
    assert response.json()["filename"] == "faq.txt"


async def test_tenant_isolation(
    hotel: Operator,
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
) -> None:
    """Another business can't see, delete or search our documents (NFR03-02)."""
    doc = (await hotel.upload()).json()
    rival = await _operator(live_client, factory, db, "owner@rival.example.com")

    # Our profile and document are "not found" to them, exactly like missing ones.
    as_rival = Operator(live_client, rival.headers, hotel.profile)
    assert (await as_rival.get("/documents")).status_code == 404
    assert (await as_rival.get(f"/documents/{doc['id']}")).status_code == 404
    response = await live_client.post(
        f"{hotel.base}/knowledge/ask", json={"question": "cancellation?"}, headers=rival.headers
    )
    assert response.status_code == 404
    # Their own profile with the same domain finds nothing of ours.
    assert (await rival.get(f"/documents/{doc['id']}")).status_code == 404
    assert (await rival.ask("What is your cancellation policy?"))["status"] == "no_answer"


async def test_login_required(live_client: httpx.AsyncClient) -> None:
    response = await live_client.get(f"/api/v1/profiles/{uuid.uuid4()}/documents")
    assert response.status_code == 401


async def test_failed_save_keeps_the_previous_version_searchable(
    hotel: Operator, live_app: FastAPI, db: AsyncSession, integration_settings: Settings
) -> None:
    """A replacement that fails half way leaves the old document intact (FR-05-06)."""
    await hotel.upload()
    store: ChromaVectorStore = live_app.state.vector_store
    tenant_id = hotel.profile.tenant_id  # the rollback below expires the loaded profile
    before = await store.count(tenant_id)
    service = KnowledgeService(
        db, SystemClock(), integration_settings, store, live_app.state.embedder
    )
    profile = await db.get_one(BusinessProfile, hotel.profile.id)

    async def broken_commit() -> None:
        raise RuntimeError("database went away")

    original_commit = db.commit
    db.commit = broken_commit  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await service.upload(profile, Upload("policies.md", "text/plain", b"New text."))
    db.commit = original_commit  # type: ignore[method-assign]

    assert await store.count(tenant_id) == before
    result = await hotel.ask("What is your cancellation policy?")
    assert "cancelled free of charge" in result["message"]


def test_file_names_are_cleaned() -> None:
    assert clean_filename("../../etc/passwd") == "passwd"
    with pytest.raises(ValidationError):
        clean_filename("  ")
    with pytest.raises(ValidationError):
        clean_filename("a" * 256 + ".txt")


async def test_profile_facts_answer_without_any_document(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> None:
    """Opening hours, policies and FAQs come from the profile itself (ADR-0005)."""
    hotel = await _operator(live_client, factory, db, profile_facts=True)

    hours = await hotel.ask("What are your opening hours?")
    assert hours["status"] == "completed"
    assert "Opening hours: Monday" in hours["message"]
    assert hours["data"]["sources"][0]["filename"] == "business profile"

    breakfast = await hotel.ask("Is breakfast included?")
    assert "buffet breakfast is included" in breakfast["message"]
    assert (await hotel.ask("Is there a swimming pool?"))["status"] == "no_answer"
