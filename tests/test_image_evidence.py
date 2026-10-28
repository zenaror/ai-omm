import asyncio
import base64
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from PIL import Image

from omm.backup_worker import backup_once, _validate_sources, BackupError
from omm.cli import main
from omm.errors import OMMError
from omm.image_evidence import CARD_SUFFIX, MAX_IMAGE_BYTES, inspect_image
from omm.image_transfer import MAX_UPLOAD_BYTES, multipart_upload, parse_upload, upload_to_server
from omm.restore import restore_from_git
from omm.service import OMM
from omm.source_documents import read_source_markdown
from omm.web_server import make_handler


def image_bytes(fmt="PNG", size=(160, 144), color="green"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=fmt)
    return output.getvalue()


class ImageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "data"
        self.env = patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false", "OMM_IMAGE_SCOPE_QUOTA_MIB": "256"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.omm = OMM(self.root)
        self.omm.init()
        self.meta = {"title": "Tela de teste", "description": "Quadrado sintético verde; não é evidência de hardware.",
                     "origin": "Fixture do teste", "evidence_type": "synthetic", "limitations": "Sem aparelho real."}

    def test_formats_roundtrip_search_preview_and_original_hash(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            with self.subTest(fmt=fmt):
                raw = image_bytes(fmt, (640, 576))
                result = self.omm.import_image("demo", raw, self.meta)
                original, mime, info = self.omm.read_image(result["path"], "original")
                self.assertEqual(original, raw)
                self.assertEqual(info["image"]["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertTrue(mime.startswith("image/"))
                preview, mime, _ = self.omm.read_image(result["path"])
                self.assertEqual(mime, "image/png")
                with Image.open(io.BytesIO(preview)) as image:
                    self.assertLessEqual(max(image.size), 512)
                self.assertNotEqual(preview, raw)
                self.assertEqual((self.root / info["original_path"]).read_bytes(), raw)
                hits = self.omm.search_sources("sintético", scopes=["demo"])
                self.assertTrue(any(hit.path == result["path"] for hit in hits))
                self.assertFalse(self.omm.search_sources("mime_type", scopes=["demo"]))
        listing = self.omm.list_images("demo", limit=2)
        self.assertEqual(listing["total"], 3)
        self.assertEqual(listing["next_offset"], 2)
        self.assertEqual(len(self.omm.list_images("demo", offset=2)["items"]), 1)
        self.assertNotIn("base64", json.dumps(listing))

    def test_identical_uploads_are_idempotent_and_distinct_provenance_reuses_original(self):
        raw = image_bytes()
        first = self.omm.import_image("demo", raw, self.meta)
        stamp = (self.root / first["path"]).stat().st_mtime_ns
        second = self.omm.import_image("demo", raw, self.meta)
        self.assertEqual(second["status"], "already_present")
        self.assertEqual(second["recorded_at"], first["recorded_at"])
        self.assertEqual((self.root / first["path"]).stat().st_mtime_ns, stamp)
        third = self.omm.import_image("demo", raw, {**self.meta, "origin": "Outro procedimento"})
        self.assertNotEqual(first["path"], third["path"])
        self.assertEqual(first["original_path"], third["original_path"])
        self.assertTrue(third["original_reused"])
        with self.assertRaises(OMMError):
            self.omm.delete_image(first["path"], "0" * 64)
        result = self.omm.delete_image(first["path"], first["sha256"])
        self.assertTrue(result["original_retained"])
        self.assertEqual(self.omm.read_image(third["path"], "original")[0], raw)
        self.omm.delete_image(third["path"], third["sha256"])
        self.assertFalse((self.root / third["original_path"]).exists())
        self.assertEqual(self.omm.search_sources("sintético", scopes=["demo"]), [])

    def test_bad_images_and_metadata_are_rejected_without_writes(self):
        animated = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(animated, format="PNG", save_all=True,
                                             append_images=[Image.new("RGB", (2, 2), "blue")])
        for raw in (b"", b"<svg onload='alert(1)'/>", b"not a JPEG", image_bytes()[:30],
                    b"x" * (MAX_IMAGE_BYTES + 1), image_bytes(size=(8193, 1)), animated.getvalue()):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                self.omm.import_image("demo", raw, self.meta)
        for changes in ({"title": ""}, {"unexpected": "field"}, {"description": "x" * 4001},
                        {"git_revision": "abcdef"}, {"captured_at": "2026-10-26T10:00:00"},
                        {"evidence_type": "confirmed"}, {"title": "two\nlines"}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                self.omm.import_image("demo", image_bytes(), {**self.meta, **changes})
        with self.assertRaises(ValueError):
            self.omm.import_image("../escape", image_bytes(), self.meta)
        self.assertFalse((self.root / "sources").exists())

    def test_symlinks_tampering_and_generic_source_edits_are_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "sources").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.omm.import_image("demo", image_bytes(), self.meta)
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / "sources").unlink()
        result = self.omm.import_image("demo", image_bytes(), self.meta)
        path, digest = result["path"], result["sha256"]
        self.assertIn("Tela", read_source_markdown(self.root, path, 1, 4)["content"])
        for operation in (lambda: self.omm.replace_source(path, "changed", digest),
                          lambda: self.omm.patch_source(path, [{"old_text": "Tela", "new_text": "Other"}], digest),
                          lambda: self.omm.delete_source(path, digest),
                          lambda: self.omm.redact_source_spans(path, [{"line": 1, "start_column": 1, "end_column": 2}], digest)):
            with self.assertRaises(ValueError):
                operation()
        blob = self.root / result["original_path"]
        blob.write_bytes(image_bytes(color="blue"))
        with self.assertRaises(ValueError):
            self.omm.read_image(path)
        with self.assertRaises(BackupError):
            _validate_sources(self.root)
        blob.unlink()
        blob.symlink_to(outside / "missing.png")
        with self.assertRaises(ValueError):
            self.omm.read_image(path)

    def test_quota_and_failed_card_write_leave_no_new_original(self):
        rng = random.Random(42)
        output = io.BytesIO()
        Image.frombytes("RGB", (512, 512), rng.randbytes(512 * 512 * 3)).save(output, format="PNG")
        raw = output.getvalue()
        with patch.dict(os.environ, {"OMM_IMAGE_SCOPE_QUOTA_MIB": "1"}):
            self.omm.import_image("demo", raw, self.meta)
            self.assertEqual(self.omm.import_image("demo", raw, self.meta)["status"], "already_present")
            output = io.BytesIO()
            Image.frombytes("RGB", (512, 512), rng.randbytes(512 * 512 * 3)).save(output, format="PNG")
            with self.assertRaisesRegex(ValueError, "Limite"):
                self.omm.import_image("demo", output.getvalue(), self.meta)
        from omm.image_evidence import _atomic_create
        def fail_card(path, data):
            if path.name.endswith(CARD_SUFFIX):
                raise OSError("fixture failure")
            _atomic_create(path, data)
        with patch("omm.image_evidence._atomic_create", side_effect=fail_card), self.assertRaises(OSError):
            self.omm.import_image("failed", image_bytes(), self.meta)
        self.assertEqual(list((self.root / "sources/failed/evidence").iterdir()), [])

    def test_original_metadata_is_preserved_but_preview_has_no_exif(self):
        output = io.BytesIO()
        exif = Image.Exif()
        exif[270] = "synthetic metadata"
        Image.new("RGB", (40, 40), "white").save(output, format="JPEG", exif=exif)
        raw = output.getvalue()
        result = self.omm.import_image("demo", raw, self.meta)
        self.assertEqual(self.omm.read_image(result["path"], "original")[0], raw)
        with Image.open(io.BytesIO(self.omm.read_image(result["path"])[0])) as preview:
            self.assertFalse(preview.getexif())

    def test_backup_restore_and_scope_merge_preserve_image_bytes_and_relative_links(self):
        remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        git("init", "-b", "main")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("remote", "add", "origin", str(remote))
        raw = image_bytes()
        result = self.omm.import_image("demo", raw, self.meta)
        backup_once(self.root, "origin", "main", True, "", "", "Test", "test@example.invalid")
        restored = self.base / "restored"
        restore_from_git(restored, str(remote), "main")
        restored_omm = OMM(restored)
        self.assertEqual(restored_omm.read_image(result["path"], "original")[0], raw)
        self.assertTrue(restored_omm.search_sources("sintético", scopes=["demo"]))
        plan = restored_omm.merge_scope("demo", "new-project", True)
        restored_omm.merge_scope("demo", "new-project", False, plan["plan_sha256"])
        moved = restored_omm.list_images("new-project")["items"][0]
        self.assertEqual(restored_omm.read_image(moved["path"], "original")[0], raw)
        _validate_sources(restored)

    def test_manifest_reports_partial_results_and_retry_does_not_duplicate(self):
        (self.base / "screen.png").write_bytes(image_bytes())
        manifest = self.base / "manifest.json"
        manifest.write_text(json.dumps([{"file": "screen.png", "metadata": self.meta},
                                        {"file": "missing.png", "metadata": self.meta}]))
        args = ["--root", str(self.root), "image-import-batch", "--scope", "demo", "--manifest", str(manifest)]
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(args), 2)
        result = json.loads(output.getvalue())
        self.assertEqual((result["status"], result["failed"]), ("partial", 1))
        self.assertTrue(result["items"][0]["ok"])
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(args), 2)
        self.assertEqual(json.loads(output.getvalue())["items"][0]["result"]["status"], "already_present")
        self.assertEqual(self.omm.list_images("demo")["total"], 1)

    def test_multipart_transport_is_binary_and_rejects_extra_fields(self):
        raw = image_bytes()
        content_type, body = multipart_upload("demo", raw, self.meta)
        scope, received, meta = parse_upload(content_type, body)
        self.assertEqual((scope, received, meta["title"]), ("demo", raw, self.meta["title"]))
        self.assertIn(raw, body)
        with self.assertRaises(ValueError):
            parse_upload("text/plain", body)
        with self.assertRaises(ValueError):
            parse_upload(content_type, body.replace(b'name="metadata"', b'name="scope"'))

    def test_http_auth_upload_read_delete_and_origin_protection(self):
        with patch.dict(os.environ, {"OMM_WEB_USERNAME": "test", "OMM_WEB_PASSWORD": "fixture-password", "OMM_MCP_TOKEN": "fixture-token"}):
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.omm))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]
            def request(method, path, body=None, headers=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(method, path, body, headers or {})
                response = conn.getresponse()
                result = response.status, dict(response.getheaders()), response.read()
                conn.close()
                return result
            content_type, body = multipart_upload("demo", image_bytes(), self.meta)
            self.assertEqual(request("POST", "/api/images", body, {"Content-Type": content_type})[0], 401)
            headers = {"Authorization": "Bearer fixture-token", "Content-Type": content_type}
            self.assertEqual(request("POST", "/api/images", body, {**headers, "Origin": "https://unrelated.invalid"})[0], 403)
            self.assertEqual(request("GET", "/api/images?scope=demo", headers={"Authorization": "Bearer wrong"})[0], 401)
            result = upload_to_server(f"http://127.0.0.1:{port}", "demo", image_bytes(), self.meta)
            from urllib.parse import urlencode
            path = "/api/images/content?" + urlencode({"path": result["path"], "variant": "original"})
            status, response_headers, raw = request("GET", path, headers={"Authorization": "Bearer fixture-token"})
            self.assertEqual(status, 200)
            self.assertEqual(raw, image_bytes())
            self.assertEqual(response_headers["X-Content-Type-Options"], "nosniff")
            basic = "Basic " + base64.b64encode(b"test:fixture-password").decode()
            self.assertEqual(request("GET", "/api/images?scope=demo", headers={"Authorization": basic})[0], 200)
            self.assertEqual(request("POST", "/api/images", b"", {**headers, "Content-Length": str(MAX_UPLOAD_BYTES + 1)})[0], 413)
            deleted = request("DELETE", "/api/images", json.dumps({"path": result["path"], "expected_sha256": result["sha256"]}),
                              {"Authorization": "Bearer fixture-token", "Content-Type": "application/json"})
            self.assertEqual(deleted[0], 200)
            self.assertEqual(request("GET", path, headers={"Authorization": basic})[0], 404)

    def test_mcp_returns_image_content_only_when_requested(self):
        try:
            from omm.mcp_server import build_server
            import mcp
        except ImportError:
            self.skipTest("Install optional MCP extra")
        result = self.omm.import_image("demo", image_bytes(), self.meta)
        async def run():
            server, _ = build_server(self.root)
            listing = await server.call_tool("list_image_evidence", {"scope": "demo"})
            self.assertTrue(all(item.type == "text" for item in listing.content))
            actual = await server.call_tool("read_image_evidence", {"path": result["path"], "variant": "original"})
            images = [item for item in actual.content if item.type == "image"]
            self.assertEqual(len(images), 1)
            self.assertEqual(base64.b64decode(images[0].data), image_bytes())
            from omm.usage import UsageRecorder
            from mcp.types import ImageContent, TextContent
            text = TextContent(type="text", text="Evidence metadata")
            small = [text, ImageContent(type="image", data="YQ==", mime_type="image/png")]
            large = [text, ImageContent(type="image", data="YQ==" * 10000, mime_type="image/png")]
            self.assertGreater(UsageRecorder._size(small), 0)
            self.assertEqual(UsageRecorder._size(small), UsageRecorder._size(large))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
