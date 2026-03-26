#!/usr/bin/env bash
# End-to-end test for the Tus resumable upload pipeline.
# Tests: session creation (via middleware), submission, tus upload, webhook, FileAsset.
set -euo pipefail

BASE="https://rnaseek.ca"
TUS="http://127.0.0.1:1080"
COOKIE_JAR=$(mktemp)

echo "=== Step 1: Get session cookie (visit home page) ==="
curl -s -c "$COOKIE_JAR" -b "$COOKIE_JAR" "$BASE/" -o /dev/null
SESSION_ID=$(grep Session_ID "$COOKIE_JAR" | awk '{print $NF}')
CSRF_TOKEN=$(grep csrftoken "$COOKIE_JAR" | awk '{print $NF}')
echo "Session_ID: $SESSION_ID"
echo "CSRF token: $CSRF_TOKEN"

echo "=== Step 2: Create submission ==="
SUB_RESP=$(curl -s -c "$COOKIE_JAR" -b "$COOKIE_JAR" "$BASE/api/submission/create" \
  -X POST -H "X-CSRFToken: $CSRF_TOKEN" -H "Referer: $BASE/" -H "Content-Type: application/json")
echo "Response: $SUB_RESP"
SUBID=$(echo "$SUB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['submission_id'])")
echo "Submission: $SUBID"

echo "=== Step 3: Create test file ==="
TEST_FILE=$(mktemp)
echo "FAKE_FASTQ_CONTENT_FOR_E2E_TEST" > "$TEST_FILE"
FILE_SIZE=$(wc -c < "$TEST_FILE" | tr -d ' ')
echo "File: $TEST_FILE ($FILE_SIZE bytes)"

echo "=== Step 4: Tus - create upload ==="
CREATE_RESP=$(curl -s -i -X POST "$TUS/files/" \
  -H "Tus-Resumable: 1.0.0" \
  -H "Upload-Length: $FILE_SIZE" \
  -H "Upload-Metadata: filename $(echo -n 'e2e_test.fastq.gz' | base64 -w0),submission_id $(echo -n "$SUBID" | base64 -w0),file_role $(echo -n 'RAW_FASTQ' | base64 -w0)" \
  -H "Cookie: Session_ID=$SESSION_ID")
echo "$CREATE_RESP" | head -15
UPLOAD_URL=$(echo "$CREATE_RESP" | grep -i '^Location:' | tr -d '\r' | awk '{print $2}')
echo "Upload URL: $UPLOAD_URL"

echo "=== Step 5: Tus - upload data ==="
PATCH_RESP=$(curl -s -i -X PATCH "$UPLOAD_URL" \
  -H "Tus-Resumable: 1.0.0" \
  -H "Upload-Offset: 0" \
  -H "Content-Type: application/offset+octet-stream" \
  -H "Cookie: Session_ID=$SESSION_ID" \
  --data-binary @"$TEST_FILE")
echo "$PATCH_RESP" | head -15

echo "=== Step 6: Wait for webhook (3s) ==="
sleep 3

echo "=== Step 7: Check FileAsset ==="
python3 -c "
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from pipeline.models import FileAsset
assets = FileAsset.objects.filter(submission__submission_id='$SUBID')
print(f'FileAssets found: {assets.count()}')
for a in assets:
    print(f'  id={a.id} role={a.file_role} path={a.local_path} exists={os.path.isfile(a.local_path)}')
if assets.count() == 0:
    print('FAIL: No FileAsset created!')
    sys.exit(1)
else:
    print('SUCCESS: Upload pipeline works end-to-end!')
"

rm -f "$TEST_FILE" "$COOKIE_JAR"
