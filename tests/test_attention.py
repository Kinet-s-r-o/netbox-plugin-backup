import unittest
from types import SimpleNamespace

from netbox_config_backup.services.attention import (
    CACHE_SECONDS,
    Attention,
    attention_for_user,
)


class DictCache:
    def __init__(self):
        self.values = {}
        self.timeouts = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, timeout):
        self.values[key] = value
        self.timeouts[key] = timeout


def authenticated(pk=1):
    return SimpleNamespace(pk=pk, is_authenticated=True)


class AttentionTests(unittest.TestCase):
    def test_total_adds_every_category(self):
        self.assertEqual(Attention(failed_targets=2, storages=1, stuck_runs=3).total, 6)
        self.assertEqual(Attention().total, 0)

    def test_anonymous_users_never_get_a_marker_or_a_query(self):
        calls = []
        result = attention_for_user(
            SimpleNamespace(pk=None, is_authenticated=False),
            compute=lambda user: calls.append(user),
            cache=DictCache(),
        )

        self.assertEqual(result, Attention())
        self.assertEqual(calls, [])

    def test_result_is_cached_briefly_per_user(self):
        cache = DictCache()
        calls = []

        def compute(user):
            calls.append(user.pk)
            return Attention(failed_targets=user.pk)

        first = attention_for_user(authenticated(3), compute=compute, cache=cache)
        again = attention_for_user(authenticated(3), compute=compute, cache=cache)
        other = attention_for_user(authenticated(5), compute=compute, cache=cache)

        self.assertEqual((first.failed_targets, again.failed_targets), (3, 3))
        self.assertEqual(other.failed_targets, 5)
        self.assertEqual(calls, [3, 5])
        self.assertEqual(set(cache.timeouts.values()), {CACHE_SECONDS})

    def test_an_all_clear_result_is_cached_too(self):
        cache = DictCache()
        calls = []

        def compute(user):
            calls.append(user.pk)
            return Attention()

        attention_for_user(authenticated(), compute=compute, cache=cache)
        attention_for_user(authenticated(), compute=compute, cache=cache)

        self.assertEqual(calls, [1])

    def test_errors_are_swallowed_so_pages_keep_rendering(self):
        def compute(_user):
            raise RuntimeError("database unavailable")

        with self.assertLogs("netbox_config_backup.services.attention", level="WARNING"):
            result = attention_for_user(authenticated(), compute=compute, cache=DictCache())

        self.assertEqual(result, Attention())

    def test_a_broken_cache_does_not_hide_the_computed_state(self):
        class BrokenCache(DictCache):
            def get(self, key):
                raise ConnectionError("redis down")

            def set(self, key, value, timeout):
                raise ConnectionError("redis down")

        with self.assertLogs("netbox_config_backup.services.attention", level="WARNING"):
            result = attention_for_user(
                authenticated(),
                compute=lambda _user: Attention(failed_targets=1),
                cache=BrokenCache(),
            )

        self.assertEqual(result, Attention(failed_targets=1))


if __name__ == "__main__":
    unittest.main()
