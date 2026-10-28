import uuid
from pathlib import Path

import pytest

from app import cli
from app.profiles.schema import Domain


def test_create_operator_needs_exactly_one_business_option() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["create-operator", "--email", "a@example.com", "--name", "A"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "create-operator",
                "--business", "Grand Hotel",
                "--tenant-id", str(uuid.uuid4()),
                "--email", "a@example.com",
                "--name", "A",
            ]
        )  # fmt: skip


def test_create_operator_parses_tenant_id() -> None:
    tenant_id = uuid.uuid4()

    args = cli.build_parser().parse_args(
        ["create-operator", "--tenant-id", str(tenant_id), "--email", "a@x.com", "--name", "A"]
    )

    assert args.tenant_id == tenant_id
    assert args.business is None


def test_invalid_email_fails_before_asking_for_a_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_prompt(_: str) -> str:
        raise AssertionError("should not prompt")

    monkeypatch.setattr("getpass.getpass", no_prompt)

    code = cli.main(["create-operator", "--business", "B", "--email", "nope", "--name", "A"])

    assert code == 1
    assert "Invalid email" in capsys.readouterr().err


def test_password_prompt_retries_until_valid_and_confirmed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["short", "long-enough-1", "different-2", "long-enough-1", "long-enough-1"])
    monkeypatch.setattr("getpass.getpass", lambda _: next(answers))

    assert cli._prompt_password() == "long-enough-1"
    errors = capsys.readouterr().err
    assert "at least 8" in errors
    assert "don't match" in errors


def test_seed_profiles_defaults_to_every_domain_and_accepts_one() -> None:
    tenant_id = str(uuid.uuid4())
    parser = cli.build_parser()

    assert parser.parse_args(["seed-profiles", "--tenant-id", tenant_id]).domain is None
    one = parser.parse_args(["seed-profiles", "--tenant-id", tenant_id, "--domain", "clinic"])
    assert one.domain == [Domain.CLINIC]
    with pytest.raises(SystemExit):
        parser.parse_args(["seed-profiles", "--tenant-id", tenant_id, "--domain", "spa"])


def test_the_committed_openapi_schema_is_current() -> None:
    """The dashboard's client is generated from docs/api/openapi.json (CLAUDE.md §5).
    If this fails, run `python -m app.cli export-openapi` and `npm run gen:api`."""
    assert cli.OPENAPI_PATH.read_text(encoding="utf-8") == cli.openapi_document()


def test_export_openapi_writes_the_schema(tmp_path: Path) -> None:
    target = tmp_path / "api" / "openapi.json"
    cli.export_openapi(target)
    assert '"/api/v1/profiles/{profile_id}/analytics"' in target.read_text(encoding="utf-8")
