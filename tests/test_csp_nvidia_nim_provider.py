from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from csp_studio.providers import NvidiaNimProvider, ProviderError, get_provider


class NvidiaNimProviderTests(unittest.TestCase):
    def test_structured_json_chat_adds_nim_payload_fields(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"model": "mock", "choices": [{"message": {"content": "{}"}}]},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(
            api_key="key",
            base_url="https://nim.test/v1",
            structured_json=True,
            client=client,
        )
        provider.chat([{"role": "user", "content": "Return JSON"}], model="mock")

        self.assertTrue(provider.structured_json)
        self.assertEqual(seen["response_format"], {"type": "json_object"})
        self.assertEqual(seen["chat_template_kwargs"], {"enable_thinking": False})
        client.close()

    def test_unstructured_chat_omits_nim_structured_payload_fields(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"model": "mock", "choices": [{"message": {"content": "ok"}}]},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(
            api_key="key",
            base_url="https://nim.test/v1",
            structured_json=False,
            client=client,
        )
        provider.chat([{"role": "user", "content": "Regular chat"}], model="mock")

        self.assertFalse(provider.structured_json)
        self.assertNotIn("response_format", seen)
        self.assertNotIn("chat_template_kwargs", seen)
        client.close()

    def test_chat_uses_openai_compatible_endpoint_and_bearer_key(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "model": "mock-nemotron",
                    "choices": [{"message": {"role": "assistant", "content": "READY"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(api_key="secret-test-key", base_url="https://nim.test/v1", client=client)
        response = provider.chat([{"role": "user", "content": "Check readiness"}], model="mock-nemotron")

        self.assertEqual(seen["url"], "https://nim.test/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer secret-test-key")
        self.assertEqual(seen["json"]["model"], "mock-nemotron")
        self.assertEqual(seen["json"]["messages"][0]["content"], "Check readiness")
        self.assertEqual(response.text, "READY")
        self.assertEqual(response.provider, "nvidia_nim")
        self.assertEqual(response.usage["completion_tokens"], 1)
        client.close()

    def test_retired_chat_model_is_migrated_to_current_default(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen["model"] = payload["model"]
            return httpx.Response(
                200,
                json={"model": payload["model"], "choices": [{"message": {"content": "ok"}}]},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(
            api_key="key",
            base_url="https://nim.test/v1",
            chat_model="nvidia/llama-3.3-nemotron-super-49b-v1.5",
            client=client,
        )
        provider.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(seen["model"], "nvidia/nemotron-3.5-lightning-30b-a3b")
        self.assertEqual(provider.chat_model, "nvidia/nemotron-3.5-lightning-30b-a3b")
        client.close()

    def test_api_key_trims_surrounding_whitespace(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(
                200,
                json={
                    "model": "mock",
                    "choices": [{"message": {"content": "ok"}}],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(api_key="  secret-test-key \r\n", base_url=" https://nim.test/v1/ ", client=client)
        provider.chat([{"role": "user", "content": "hello"}], model="mock")
        self.assertEqual(seen["auth"], "Bearer secret-test-key")
        self.assertEqual(provider.base_url, "https://nim.test/v1")
        client.close()

    def test_api_key_rejects_internal_whitespace(self) -> None:
        with self.assertRaises(ProviderError):
            NvidiaNimProvider(api_key="secret test key")

    def test_missing_key_fails_before_network_request(self) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(500)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(api_key="", client=client)
        provider.api_key = None
        with self.assertRaises(ProviderError):
            provider.chat([{"role": "user", "content": "hello"}])
        self.assertFalse(called)
        client.close()

    def test_vision_encodes_local_image_as_data_uri(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.update(payload)
            return httpx.Response(
                200,
                json={
                    "model": "mock-vlm",
                    "choices": [{"message": {"content": "Frames are too similar."}}],
                },
            )

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "scene-03.png"
            Image.new("RGB", (16, 16), "white").save(image)
            expected_prefix = "data:image/png;base64,"

            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = NvidiaNimProvider(api_key="key", base_url="https://nim.test/v1", vision_model="mock-vlm", client=client)
            result = provider.analyze_images("Compare these scenes", [str(image)])

            content = seen["messages"][0]["content"]
            self.assertEqual(content[0], {"type": "text", "text": "Compare these scenes"})
            data_uri = content[1]["image_url"]["url"]
            self.assertTrue(data_uri.startswith(expected_prefix))
            decoded = base64.b64decode(data_uri.removeprefix(expected_prefix))
            self.assertGreater(len(decoded), 20)
            self.assertEqual(result.text, "Frames are too similar.")
            client.close()

    def test_vision_retries_one_read_timeout(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("slow vision response", request=request)
            return httpx.Response(
                200,
                json={
                    "model": "mock-vlm",
                    "choices": [{"message": {"content": "QA complete"}}],
                },
            )

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "scene.png"
            Image.new("RGB", (16, 16), "white").save(image)
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = NvidiaNimProvider(
                api_key="key",
                base_url="https://nim.test/v1",
                vision_model="mock-vlm",
                vision_timeout=240,
                vision_retries=1,
                client=client,
            )
            result = provider.analyze_images("Review", [str(image)])
            self.assertEqual(calls, 2)
            self.assertEqual(result.text, "QA complete")
            client.close()

    def test_vision_timeout_error_is_actionable_after_retries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("still slow", request=request)

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "scene.png"
            Image.new("RGB", (16, 16), "white").save(image)
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = NvidiaNimProvider(
                api_key="key",
                base_url="https://nim.test/v1",
                vision_timeout=123,
                vision_retries=1,
                client=client,
            )
            with self.assertRaisesRegex(ProviderError, r"timed out after 123s.*2 attempt"):
                provider.analyze_images("Review", [str(image)])
            client.close()

    def test_embeddings_preserve_backend_index_order(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [3, 4]},
                        {"index": 0, "embedding": [1, 2]},
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = NvidiaNimProvider(api_key="key", base_url="https://nim.test/v1", embed_model="mock-embed", client=client)
        vectors = provider.embed(["alpha", "beta"], input_type="passage")
        self.assertEqual(seen["model"], "mock-embed")
        self.assertEqual(seen["input"], ["alpha", "beta"])
        self.assertEqual(seen["input_type"], "passage")
        self.assertEqual(vectors, [[1.0, 2.0], [3.0, 4.0]])
        client.close()

    def test_registry_alias_builds_nvidia_provider(self) -> None:
        provider = get_provider("nim", api_key="key")
        try:
            self.assertIsInstance(provider, NvidiaNimProvider)
            self.assertFalse(provider.structured_json)
        finally:
            provider.close()


if __name__ == "__main__":
    unittest.main()
