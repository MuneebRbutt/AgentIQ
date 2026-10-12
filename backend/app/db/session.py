from sqlalchemy import create_engine, text

from app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)


def check_database_connection() -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return True