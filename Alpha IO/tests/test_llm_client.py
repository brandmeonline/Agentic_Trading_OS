"""Tests for the single LLM access point and the call sites it replaced.

The invariant that matters: importing any LLM-backed module must succeed with no
key and no SDK, and calling one without a key must raise a typed, actionable
error rather than an AttributeError from an SDK symbol that no longer exists.
"""

import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import llm_client
from core.llm_client import LLMUnavailable


class _NoKey(unittest.TestCase):
    """Base that guarantees no credential is visible to the test."""

    def setUp(self):
        self._saved = os.environ.pop("OPENAI_API_KEY", None)
        llm_client.reset_client()

    def tearDown(self):
        if self._saved is not None:
            os.environ["OPENAI_API_KEY"] = self._saved
        else:
            os.environ.pop("OPENAI_API_KEY", None)
        llm_client.reset_client()


class TestConfiguration(_NoKey):
    def test_not_configured_without_key(self):
        self.assertFalse(llm_client.is_configured())
        self.assertIsNone(llm_client.api_key())

    def test_blank_key_is_not_a_key(self):
        os.environ["OPENAI_API_KEY"] = "   "
        self.assertFalse(llm_client.is_configured())

    def test_configured_with_key(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertTrue(llm_client.is_configured())
        self.assertEqual(llm_client.api_key(), "sk-test")


class TestTypedFailure(_NoKey):
    def test_get_client_raises_typed_error(self):
        with self.assertRaises(LLMUnavailable) as ctx:
            llm_client.get_client()
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_chat_raises_typed_error(self):
        with self.assertRaises(LLMUnavailable):
            llm_client.chat([{"role": "user", "content": "hi"}])

    def test_embed_raises_typed_error(self):
        with self.assertRaises(LLMUnavailable):
            llm_client.embed("hello")

    def test_chat_rejects_empty_messages_before_touching_credentials(self):
        with self.assertRaises(ValueError):
            llm_client.chat([])

    def test_embed_rejects_empty_text_before_touching_credentials(self):
        with self.assertRaises(ValueError):
            llm_client.embed("   ")


class TestImportsAreFree(_NoKey):
    """Every module that used the removed pre-1.0 API must still import."""

    def test_modules_import_without_credentials(self):
        for name in ("core.llm_client", "utils.rag_macro", "core.signal_router",
                     "core.asymmetry_index", "core.score_signals"):
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_no_module_references_the_removed_sdk_symbols(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # llm_client is the module that documents the migration away from these
        # symbols, so it names them in prose. Everything else must not.
        exempt = {"core/llm_client.py"}
        offenders = []
        for folder in ("core", "utils"):
            directory = os.path.join(root, folder)
            for filename in sorted(os.listdir(directory)):
                if not filename.endswith(".py") or f"{folder}/{filename}" in exempt:
                    continue
                path = os.path.join(directory, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    body = handle.read()
                if "openai.ChatCompletion" in body or "openai.Embedding" in body:
                    offenders.append(f"{folder}/{filename}")
        self.assertEqual(offenders, [], f"pre-1.0 openai API still referenced in: {offenders}")


class TestClientCaching(_NoKey):
    def test_reset_clears_cached_client(self):
        llm_client._client = object()
        llm_client._client_key = "sk-old"
        llm_client.reset_client()
        self.assertIsNone(llm_client._client)
        self.assertIsNone(llm_client._client_key)


if __name__ == "__main__":
    unittest.main()
