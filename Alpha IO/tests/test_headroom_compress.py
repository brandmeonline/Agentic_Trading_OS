"""Safety tests for the opt-in Headroom compression adapter.

The invariant that matters: with the feature off (the default), compression is the
identity function, so no LLM call path can change behaviour or outputs.
"""

import builtins
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import headroom_compress as hc

_MESSAGES = [
    {"role": "system", "content": "You are a trading signal generator."},
    {"role": "user", "content": "Generate new trading alpha signals from these examples."},
]


class TestHeadroomAdapter(unittest.TestCase):
    def setUp(self):
        for env in ("ALPHAIO_HEADROOM", "HEADROOM_ENABLED"):
            os.environ.pop(env, None)

    def tearDown(self):
        for env in ("ALPHAIO_HEADROOM", "HEADROOM_ENABLED"):
            os.environ.pop(env, None)

    def test_disabled_by_default_is_identity(self):
        out = hc.compress_messages(_MESSAGES, surface="signal_augment")
        self.assertIs(out, _MESSAGES)  # same object: provably no behaviour change
        self.assertFalse(hc.compression_enabled())

    def test_env_toggles_enabled(self):
        for val in ("1", "true", "YES", "on"):
            os.environ["ALPHAIO_HEADROOM"] = val
            self.assertTrue(hc.compression_enabled())
            os.environ.pop("ALPHAIO_HEADROOM")

    def test_enabled_without_dependency_degrades_to_passthrough(self):
        os.environ["ALPHAIO_HEADROOM"] = "1"
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "headroom" or name.startswith("headroom."):
                raise ImportError("headroom not installed")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            out = hc.compress_messages(_MESSAGES, surface="signal_augment")
            self.assertEqual(out, _MESSAGES)  # content preserved
        finally:
            builtins.__import__ = real_import

    def test_enabled_with_dependency_preserves_shape(self):
        try:
            import headroom  # noqa: F401
        except Exception:
            self.skipTest("headroom not installed")
        os.environ["ALPHAIO_HEADROOM"] = "1"
        out = hc.compress_messages(_MESSAGES, surface="signal_augment",
                                   compress_user_messages=True, min_tokens_to_compress=10)
        self.assertEqual(len(out), len(_MESSAGES))
        self.assertEqual([m["role"] for m in out], [m["role"] for m in _MESSAGES])


if __name__ == "__main__":
    unittest.main()
