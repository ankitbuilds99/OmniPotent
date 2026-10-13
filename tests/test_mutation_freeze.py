from app import mutation_freeze
from app.mutation_freeze import freeze, is_frozen, reset, unfreeze


class FakeRedis:
    def __init__(self):
        self.values = set()
        self.global_key = False

    def sadd(self, _key, value): self.values.add(value)
    def srem(self, _key, value): self.values.discard(value)
    def sismember(self, _key, value): return value in self.values
    def set(self, _key, _value): self.global_key = True
    def delete(self, _key): self.global_key = False
    def exists(self, _key): return self.global_key


def setup_function():
    reset()


def teardown_function():
    reset()


def test_global_freeze_blocks_all_tenants():
    freeze()
    assert is_frozen("tenant-a")
    assert is_frozen("tenant-b")


def test_tenant_freeze_is_scoped_and_reversible():
    freeze(tenant_id="tenant-a")
    assert is_frozen("tenant-a")
    assert not is_frozen("tenant-b")
    unfreeze(tenant_id="tenant-a")
    assert not is_frozen("tenant-a")


def test_shared_backend_freeze_is_visible_across_replicas():
    backend = FakeRedis()
    mutation_freeze.configure_backend(backend)
    freeze(tenant_id="shared")
    assert is_frozen("shared")
    assert not is_frozen("other")
    unfreeze(tenant_id="shared")
    assert not is_frozen("shared")
