"""Registry of ORM model modules.

Every module that defines models is imported here so that `Base.metadata` is complete
for Alembic autogenerate. Add new model modules to this list.
"""

from app.auth import models as auth_models
from app.bookings import models as booking_models
from app.channels import models as channel_models
from app.channels.email import models as email_models
from app.channels.voice import models as voice_models
from app.conversations import models as conversation_models
from app.db.base import Base
from app.escalations import models as escalation_models
from app.followups import models as followup_models
from app.knowledge import models as knowledge_models
from app.profiles import models as profile_models
from app.tenancy import models as tenancy_models

__all__ = [
    "Base",
    "auth_models",
    "booking_models",
    "channel_models",
    "conversation_models",
    "email_models",
    "escalation_models",
    "followup_models",
    "knowledge_models",
    "profile_models",
    "tenancy_models",
    "voice_models",
]
