"""Tus upload webhook — receives post-finish notifications from tusd."""

import hashlib
import hmac
import json
import logging
import os
import shutil

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from pipeline.models import AnalysisSubmission, FileAsset
from pipeline.views.api import _subdir_for_role

logger = logging.getLogger(__name__)


def _verify_hook_signature(request):
    """Verify tusd HMAC-SHA256 hook signature.

    tusd signs the request body with the secret configured via
    ``--hooks-http-secret`` and sends the signature in the
    ``Hook-Signature`` header as ``sha256=<hex>``.
    """
    secret = getattr(settings, "TUS_HOOK_SECRET", "")
    if not secret:
        return True  # no secret configured — skip verification

    sig_header = request.headers.get("Hook-Signature", "")
    if not sig_header.startswith("sha256="):
        return False

    expected_sig = sig_header[len("sha256="):]
    computed = hmac.new(
        secret.encode(), request.body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, expected_sig)


@method_decorator(csrf_exempt, name="dispatch")
class TusWebhookView(View):
    """Handle tusd ``post-finish`` webhook.

    When tusd completes a file upload it POSTs a JSON payload containing
    the upload metadata (filename, submission_id, file_role) and the
    storage path.  This view:

    1. Validates the HMAC signature.
    2. Moves the file from the tusd data directory to the correct NFS
       subdirectory under the submission's upload_dir.
    3. Creates a ``FileAsset`` record.
    """

    http_method_names = ["post"]

    def post(self, request):
        # ── Verify HMAC signature ──
        if not _verify_hook_signature(request):
            return JsonResponse({"error": "Invalid signature."}, status=403)

        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        # tusd sends {"Type": "post-finish", "Event": {...}} in v2 hooks
        # or {"Upload": {...}} in v1 hooks.  Support both.
        upload = payload.get("Upload") or (payload.get("Event") or {}).get("Upload")
        if not upload:
            return JsonResponse({"error": "Missing Upload object."}, status=400)

        meta = upload.get("MetaData") or {}
        filename = meta.get("filename", "")
        submission_id = meta.get("submission_id", "")
        file_role = meta.get("file_role", FileAsset.FileRole.RAW_FASTQ)

        if not filename or not submission_id:
            return JsonResponse(
                {"error": "Missing filename or submission_id in metadata."},
                status=400,
            )

        # ── Locate the submission (implicitly verifies session ownership) ──
        try:
            submission = AnalysisSubmission.objects.select_related("session").get(
                submission_id=submission_id,
            )
        except (AnalysisSubmission.DoesNotExist, ValueError):
            return JsonResponse({"error": "Submission not found."}, status=404)

        # ── Validate file_role ──
        valid_roles = {choice[0] for choice in FileAsset.FileRole.choices}
        if file_role not in valid_roles:
            file_role = FileAsset.FileRole.RAW_FASTQ

        # ── Resolve source path ──
        # tusd reports paths from its container; Django may see a different
        # mount point for the same volume, so always construct from our
        # TUS_DATA_DIR setting + the upload ID.
        upload_id = upload.get("ID", "")
        if not upload_id:
            return JsonResponse({"error": "Missing upload ID."}, status=400)

        tus_data_dir = getattr(settings, "TUS_DATA_DIR", "")
        tus_path = os.path.join(tus_data_dir, upload_id)

        if not os.path.isfile(tus_path):
            logger.error("Tus file not found at %s", tus_path)
            return JsonResponse({"error": "Upload file not found."}, status=400)

        # ── Sanitise filename ──
        safe_name = os.path.basename(filename)

        # ── Move to final NFS destination ──
        subdir = _subdir_for_role(file_role)
        dest_dir = os.path.join(submission.upload_dir, subdir)
        dest_path = os.path.join(dest_dir, safe_name)
        os.makedirs(dest_dir, exist_ok=True)

        try:
            shutil.move(tus_path, dest_path)
        except OSError:
            # Cross-filesystem fallback: copy + remove
            shutil.copy2(tus_path, dest_path)
            os.remove(tus_path)

        # Clean up the .info sidecar file tusd creates
        info_path = tus_path + ".info"
        if os.path.isfile(info_path):
            try:
                os.remove(info_path)
            except OSError:
                pass

        # ── Create FileAsset ──
        asset = FileAsset.objects.create(
            session=submission.session,
            submission=submission,
            file_role=file_role,
            local_path=dest_path,
            is_user_uploaded=True,
        )

        logger.info(
            "Tus upload complete: %s → %s (asset %s)",
            safe_name, dest_path, asset.id,
        )

        return JsonResponse({
            "ok": True,
            "asset_id": str(asset.id),
        })


class TusAssetLookupView(View):
    """Look up a FileAsset created by the Tus webhook.

    The frontend calls this after Uppy reports success to retrieve the
    ``asset_id`` for later deletion.

    GET /api/upload/tus-asset?submission_id=<uuid>&filename=<name>
    """

    http_method_names = ["get"]

    def get(self, request):
        session_obj = request.session_obj
        submission_id = request.GET.get("submission_id", "")
        filename = request.GET.get("filename", "")

        if not submission_id or not filename:
            return JsonResponse({"error": "Missing parameters."}, status=400)

        safe_name = os.path.basename(filename)

        try:
            asset = FileAsset.objects.get(
                session=session_obj,
                submission_id=submission_id,
                local_path__endswith="/" + safe_name,
                is_user_uploaded=True,
            )
        except FileAsset.DoesNotExist:
            return JsonResponse({"error": "Asset not found."}, status=404)
        except FileAsset.MultipleObjectsReturned:
            asset = FileAsset.objects.filter(
                session=session_obj,
                submission_id=submission_id,
                local_path__endswith="/" + safe_name,
                is_user_uploaded=True,
            ).order_by("-id").first()

        return JsonResponse({"asset_id": str(asset.id)})
