from app import execution_control


class FakeRedis:
    def __init__(self): self.data = {}
    def set(self, key, value): self.data[key] = value
    def get(self, key): return self.data.get(key)
    def delete(self, key): self.data.pop(key, None)


def setup_function(): execution_control.reset()
def teardown_function(): execution_control.reset()


def test_shared_control_request_is_visible_and_clearable():
    execution_control.configure_backend(FakeRedis())
    execution_control.request("e1", "pause")
    assert execution_control.requested("e1") == "pause"
    execution_control.clear("e1")
    assert execution_control.requested("e1") is None


def test_real_fake_redis_backend_shares_control_across_worker_reconfiguration():
    """Exercise the same synchronous API used by the production Redis client."""
    import fakeredis

    redis = fakeredis.FakeRedis(decode_responses=True)
    execution_control.configure_backend(redis)
    execution_control.request("worker-owned-execution", "cancel")

    # A second API/worker process would resolve the same Redis key after its
    # local module state was initialized; reconfiguration models that boundary.
    execution_control.configure_backend(redis)
    assert execution_control.requested("worker-owned-execution") == "cancel"

    execution_control.clear("worker-owned-execution")
    assert execution_control.requested("worker-owned-execution") is None
