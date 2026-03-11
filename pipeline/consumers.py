import json
import uuid

from channels.generic.websocket import AsyncWebsocketConsumer

from pipeline.middleware import SESSION_COOKIE_NAME


class PipelineProgressConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for real-time pipeline progress updates.

    Clients connect to: ws://.../ws/pipeline/<job_id>/
    The consumer validates that the session cookie owns the job
    before joining the channel group.
    """

    async def connect(self):
        self.job_id = self.scope["url_route"]["kwargs"]["job_id"]
        self.group_name = f"pipeline_{self.job_id}"

        # Validate session ownership
        if not await self._validate_session():
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Client messages are not expected; ignore.
        pass

    async def pipeline_progress(self, event):
        """Handle pipeline.progress messages from the channel layer."""
        await self.send(text_data=json.dumps(event["data"]))

    async def _validate_session(self):
        """Check that the requesting session owns this job."""
        from channels.db import database_sync_to_async

        from pipeline.models import AnalysisJob

        cookies = self.scope.get("cookies", {})
        raw_session_id = cookies.get(SESSION_COOKIE_NAME)
        if not raw_session_id:
            return False

        try:
            session_uuid = uuid.UUID(raw_session_id)
        except ValueError:
            return False

        @database_sync_to_async
        def check_ownership():
            return AnalysisJob.objects.filter(
                job_id=self.job_id, session_id=session_uuid
            ).exists()

        return await check_ownership()
