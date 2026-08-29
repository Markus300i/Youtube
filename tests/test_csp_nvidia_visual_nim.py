from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from csp_studio.providers import NvidiaVisualNimProvider, ProviderError


class NvidiaVisualNimTests(unittest.TestCase):
    def test_provider_requires_explicit_visual_endpoint(self):
        with self.assertRaises(ProviderError):
            NvidiaVisualNimProvider(base_url="")

    def test_flux_generation_uses_openai_compatible_endpoint_and_writes_image(self):
        seen = {}
        fake_png = b"\x89PNG\r\n\x1a\n" + b"payload"

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(fake_png).decode()}]})

        with tempfile.TemporaryDirectory() as tmp:
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = NvidiaVisualNimProvider(base_url="https://visual.test/v1", api_key="key", client=client)
            target = Path(tmp) / "candidate.png"
            result = provider.generate_image("Polish basement door", str(target), seed=12, steps=4)
            self.assertEqual(seen["url"], "https://visual.test/v1/images/generations")
            self.assertEqual(seen["json"]["model"], "black-forest-labs/flux.2-klein-4b")
            self.assertEqual(seen["json"]["response_format"], "b64_json")
            self.assertEqual(seen["json"]["seed"], 12)
            self.assertEqual(target.read_bytes(), fake_png)
            self.assertEqual(result.kind, "image_generation")
            client.close()

    def test_flux_edit_encodes_local_reference_images(self):
        seen = {}
        fake_png = b"image-output"

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(fake_png).decode()}]})

        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.png"
            second = Path(tmp) / "b.jpg"
            Image.new("RGB", (8, 8), "black").save(first)
            Image.new("RGB", (8, 8), "white").save(second)
            target = Path(tmp) / "edited.png"
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = NvidiaVisualNimProvider(base_url="https://visual.test/v1", client=client)
            result = provider.edit_images("Keep the door, change the angle", [str(first), str(second)], str(target))
            self.assertEqual(len(seen["image"]), 2)
            self.assertTrue(seen["image"][0].startswith("data:image/png;base64,"))
            self.assertTrue(seen["image"][1].startswith("data:image/jpeg;base64,"))
            self.assertEqual(result.metadata["input_images"], 2)
            self.assertEqual(target.read_bytes(), fake_png)
            client.close()

    def test_wan_i2v_uses_input_reference_and_writes_mp4(self):
        seen = {}
        fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"video"

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"b64_json": base64.b64encode(fake_mp4).decode()}})

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "scene.jpg"
            Image.new("RGB", (9, 16), "gray").save(image)
            video = Path(tmp) / "scene.mp4"
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = NvidiaVisualNimProvider(base_url="https://visual.test/v1", client=client)
            result = provider.generate_video(
                "Animate with subtle documentary camera drift",
                str(video),
                input_image=str(image),
                size="720x1280",
                seconds=4,
            )
            self.assertEqual(seen["url"], "https://visual.test/v1/videos/generations")
            self.assertEqual(seen["json"]["model"], "wan-ai/wan2.2")
            self.assertTrue(seen["json"]["input_reference"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(seen["json"]["size"], "720x1280")
            self.assertEqual(seen["json"]["seconds"], 4)
            self.assertEqual(video.read_bytes(), fake_mp4)
            self.assertEqual(result.kind, "image_to_video")
            client.close()


if __name__ == "__main__":
    unittest.main()
