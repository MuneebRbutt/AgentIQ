import uuid

from sqlalchemy import select

from app.auth.models import Operator
from app.tenancy.scoping import scoped


def test_scoped_adds_a_tenant_filter() -> None:
    tenant_id = uuid.uuid4()

    statement = scoped(select(Operator).where(Operator.is_active), Operator, tenant_id)
    compiled = statement.compile()

    assert "operators.tenant_id = :tenant_id_1" in str(compiled)
    assert compiled.params["tenant_id_1"] == tenant_id
    assert "operators.is_active" in str(compiled)
