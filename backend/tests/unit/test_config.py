import pytest

from app.core.config import Environment, Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_are_development() -> None:
    settings = make_settings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.is_production is False


def test_frontend_origin_accepts_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173, http://127.0.0.1:5173 ,")

    settings = make_settings()

    assert settings.frontend_origin == ["http://localhost:5173", "http://127.0.0.1:5173"]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@db:5432/agentiq",
        "postgresql+psycopg2://u:p@db:5432/agentiq",
        "postgresql://u:p@db:5432/agentiq",
        "postgresql+asyncpg://u:p@db:5432/agentiq",
    ],
)
def test_database_url_always_uses_asyncpg(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    settings = make_settings()

    assert settings.database_url.get_secret_value() == "postgresql+asyncpg://u:p@db:5432/agentiq"


def test_connection_strings_are_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:hunter2@db:5432/agentiq")
    monkeypatch.setenv("REDIS_URL", "redis://:hunter2@cache:6379/0")

    assert "hunter2" not in repr(make_settings())


def test_health_check_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        make_settings(health_check_timeout_seconds=0)


@pytest.mark.parametrize("secret", [None, "too-short"], ids=["dev-default", "short"])
def test_production_requires_a_real_jwt_secret(secret: str | None) -> None:
    overrides: dict[str, object] = {"environment": Environment.PRODUCTION}
    if secret is not None:
        overrides["jwt_secret_key"] = secret

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        make_settings(**overrides)


def test_production_accepts_a_long_random_jwt_secret() -> None:
    settings = make_settings(environment=Environment.PRODUCTION, jwt_secret_key="x" * 48)

    assert settings.is_production


def test_blank_jwt_secret_falls_back_to_the_dev_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "")

    assert len(make_settings().jwt_secret_key.get_secret_value()) >= 32
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        make_settings(environment=Environment.PRODUCTION)
