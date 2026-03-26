"""
Tests for TusdHookView — the tusd post-finish webhook endpoint.

Covers:
- Ignores non-post-finish hooks (returns 200)
- Registers FileAsset on valid post-finish payload
- Moves file from tusd upload dir to submission subdirectory
- Cleans up .info sidecar file
- Rejects missing/invalid JSON payload
- Rejects missing metadata keys (filename, submission_id)
- Rejects invalid submission_id / session mismatch
- Falls back to RAW_FASTQ for unknown file_role
- Returns 500 when uploaded file not found on disk
"""
import json
import os
import shutil
import tempfile

from django.test import RequestFactory, TestCase, override_settings

from pipeline.models import AnalysisSubmission, FileAsset, Session
from pipeline.views import TusdHookView


class TusdHookViewTest(TestCase):
    """Test the tusd post-finish webhook endpoint."""

    def setUp(self):
        self.factory = RequestFactory()
        self.session = Session.objects.create()
        self.submission = AnalysisSubmission.objects.create(session=self.session)
        # Create a temp dir to simulate the tusd upload directory
        self.upload_root = tempfile.mkdtemp()
        self.tus_id = "abc123def456"
        # Create the fake uploaded file
        self.tus_file_path = os.path.join(self.upload_root, self.tus_id)
        with open(self.tus_file_path, "wb") as f:
            f.write(b"FAKE_FASTQ_CONTENT")
        # Create the .info sidecar file
        self.tus_info_path = self.tus_file_path + ".info"
        with open(self.tus_info_path, "w") as f:
            f.write("{}")

    def tearDown(self):
        shutil.rmtree(self.upload_root, ignore_errors=True)
        # Clean up submission upload dir
        if os.path.isdir(self.submission.upload_dir):
            shutil.rmtree(self.submission.upload_dir, ignore_errors=True)

    def _build_payload(self, **overrides):
        """Build a valid tusd post-finish hook payload."""
        meta = {
            "filename": "sample_R1.fastq.gz",
            "submission_id": str(self.submission.submission_id),
            "file_role": "RAW_FASTQ",
        }
        meta.update(overrides.pop("meta_overrides", {}))

        payload = {
            "Type": "post-finish",
            "Event": {
                "Upload": {
                    "ID": self.tus_id,
                    "Size": 18,
                    "Offset": 18,
                    "MetaData": meta,
                    "Storage": {
                        "Type": "filestore",
                        "Path": self.tus_file_path,
                    },
                },
                "HTTPRequest": {
                    "Method": "PATCH",
                    "URI": f"/files/{self.tus_id}",
                },
            },
        }
        payload.update(overrides)
        return payload

    def _post(self, payload, hook_name="post-finish"):
        """Send a POST request to the TusdHookView."""
        req = self.factory.post(
            "/api/tusd-hooks/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOOK_NAME=hook_name,
        )
        req.session_obj = self.session
        return TusdHookView.as_view()(req)

    def _post_no_header(self, payload):
        """Send a POST without Hook-Name header (tusd v2 behaviour)."""
        req = self.factory.post(
            "/api/tusd-hooks/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        req.session_obj = self.session
        return TusdHookView.as_view()(req)

    # ── Happy path ──

    def test_post_finish_creates_file_asset(self):
        payload = self._build_payload()
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("asset_id", data)

        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.session, self.session)
        self.assertEqual(asset.submission, self.submission)
        self.assertEqual(asset.file_role, "RAW_FASTQ")
        self.assertTrue(asset.is_user_uploaded)
        self.assertIn("sample_R1.fastq.gz", asset.local_path)

    def test_post_finish_moves_file_to_submission_dir(self):
        payload = self._build_payload()
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        asset = FileAsset.objects.get(id=data["asset_id"])

        # File should exist at the new location
        self.assertTrue(os.path.isfile(asset.local_path))
        # File should NOT exist at the tusd location
        self.assertFalse(os.path.isfile(self.tus_file_path))

    def test_post_finish_cleans_info_sidecar(self):
        payload = self._build_payload()
        self._post(payload)
        # .info file should be removed
        self.assertFalse(os.path.isfile(self.tus_info_path))

    def test_post_finish_with_metadata_csv_role(self):
        payload = self._build_payload(
            meta_overrides={"file_role": "METADATA_CSV", "filename": "metadata.csv"}
        )
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "METADATA_CSV")
        self.assertIn("metadata", asset.local_path)

    def test_post_finish_with_bam_role(self):
        payload = self._build_payload(
            meta_overrides={"file_role": "ALIGNMENT_BAM", "filename": "sample.bam"}
        )
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "ALIGNMENT_BAM")
        self.assertIn("aligned", asset.local_path)

    # ── Non-post-finish hooks ──

    def test_pre_create_hook_returns_200_empty(self):
        payload = self._build_payload()
        resp = self._post(payload, hook_name="pre-create")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), {})
        # No FileAsset should be created
        self.assertEqual(FileAsset.objects.count(), 0)

    def test_post_create_hook_returns_200_empty(self):
        payload = self._build_payload()
        resp = self._post(payload, hook_name="post-create")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(FileAsset.objects.count(), 0)

    # ── Error handling ──

    def test_invalid_json_returns_400(self):
        req = self.factory.post(
            "/api/tusd-hooks/",
            data="NOT JSON",
            content_type="application/json",
            HTTP_HOOK_NAME="post-finish",
        )
        req.session_obj = self.session
        resp = TusdHookView.as_view()(req)
        self.assertEqual(resp.status_code, 400)

    def test_missing_event_key_returns_400(self):
        resp = self._post({"Type": "post-finish"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_upload_key_returns_400(self):
        resp = self._post({"Type": "post-finish", "Event": {}})
        self.assertEqual(resp.status_code, 400)

    def test_missing_filename_returns_400(self):
        payload = self._build_payload(meta_overrides={"filename": ""})
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("filename", json.loads(resp.content)["error"])

    def test_missing_submission_id_returns_400(self):
        payload = self._build_payload(meta_overrides={"submission_id": ""})
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("submission_id", json.loads(resp.content)["error"])

    def test_invalid_submission_id_returns_400(self):
        payload = self._build_payload(
            meta_overrides={"submission_id": "00000000-0000-0000-0000-000000000000"}
        )
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid submission_id", json.loads(resp.content)["error"])

    def test_session_mismatch_returns_400(self):
        other_session = Session.objects.create()
        other_submission = AnalysisSubmission.objects.create(session=other_session)
        payload = self._build_payload(
            meta_overrides={"submission_id": str(other_submission.submission_id)}
        )
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid submission_id", json.loads(resp.content)["error"])

    def test_unknown_file_role_falls_back_to_raw_fastq(self):
        payload = self._build_payload(
            meta_overrides={"file_role": "NONEXISTENT_ROLE"}
        )
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "RAW_FASTQ")

    def test_file_not_on_disk_returns_500(self):
        os.remove(self.tus_file_path)
        payload = self._build_payload()
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("not found on disk", json.loads(resp.content)["error"])

    def test_no_session_returns_400(self):
        """If the middleware didn't attach a session_obj."""
        req = self.factory.post(
            "/api/tusd-hooks/",
            data=json.dumps(self._build_payload()),
            content_type="application/json",
            HTTP_HOOK_NAME="post-finish",
        )
        # Deliberately not setting req.session_obj
        resp = TusdHookView.as_view()(req)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Session", json.loads(resp.content)["error"])

    def test_fallback_path_when_storage_path_empty(self):
        """When Storage.Path is absent, derive path from MEDIA_ROOT + uploads/<id>."""
        # Arrange: place file at the fallback location
        fallback_dir = os.path.join(self.upload_root, "uploads")
        os.makedirs(fallback_dir, exist_ok=True)
        fallback_path = os.path.join(fallback_dir, self.tus_id)
        shutil.move(self.tus_file_path, fallback_path)

        payload = self._build_payload()
        # Remove Storage.Path so the view uses the fallback
        payload["Event"]["Upload"]["Storage"] = {}

        req = self.factory.post(
            "/api/tusd-hooks/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOOK_NAME="post-finish",
        )
        req.session_obj = self.session

        # Patch MEDIA_ROOT env var for this test
        original = os.environ.get("MEDIA_ROOT")
        os.environ["MEDIA_ROOT"] = self.upload_root
        try:
            resp = TusdHookView.as_view()(req)
        finally:
            if original is not None:
                os.environ["MEDIA_ROOT"] = original
            else:
                os.environ.pop("MEDIA_ROOT", None)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("asset_id", data)

    def test_tusd_v2_no_hook_name_header(self):
        """tusd v2 sends Type in body, not as Hook-Name header."""
        payload = self._build_payload()
        resp = self._post_no_header(payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("asset_id", data)
        asset = FileAsset.objects.get(id=data["asset_id"])
        self.assertEqual(asset.file_role, "RAW_FASTQ")

    def test_tusd_v2_non_post_finish_type_ignored(self):
        """tusd v2 body with Type != post-finish returns 200 empty."""
        payload = self._build_payload()
        payload["Type"] = "pre-create"
        resp = self._post_no_header(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), {})
        self.assertEqual(FileAsset.objects.count(), 0)

    def test_cross_filesystem_move_fallback(self):
        """When shutil.move fails (cross-filesystem), copy2 + remove is used."""
        from unittest.mock import patch

        payload = self._build_payload()

        def broken_move(src, dst, *args, **kwargs):
            raise OSError("Cross-device link")

        with patch("pipeline.views.api.shutil.move", side_effect=broken_move):
            resp = self._post(payload)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        asset = FileAsset.objects.get(id=data["asset_id"])
        # File should exist at the destination
        self.assertTrue(os.path.isfile(asset.local_path))
        # Original file should be removed
        self.assertFalse(os.path.isfile(self.tus_file_path))
