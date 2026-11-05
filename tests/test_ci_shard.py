"""CI sharding (tests/conftest.py `--shard`): every test lands in exactly one shard, the same everywhere."""
from tests.conftest import shard_of


def test_shards_partition_and_are_deterministic():
    ids = [f"tests/test_x.py::test_{k}[{p}]" for k in range(200) for p in ("a", "b")]
    for n in (1, 2, 3, 4, 7):
        shards = [shard_of(i, n) for i in ids]
        assert all(0 <= s < n for s in shards)
        assert shards == [shard_of(i, n) for i in ids]           # deterministic
        if n > 1:
            assert len(set(shards)) == n                          # no empty shard on a realistic suite
