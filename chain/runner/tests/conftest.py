import pytest
from exo_guardian.store import MemoryStore
from guardian_pg import WRITER, needs_pg, reset_pg


@pytest.fixture(params=["memory", pytest.param("pg", marks=needs_pg)])
def store(request):
    if request.param == "memory":
        return MemoryStore()
    from exo_guardian.store import PgStore
    reset_pg()
    return PgStore(WRITER)


@pytest.fixture
def pg_reset():
    reset_pg()
