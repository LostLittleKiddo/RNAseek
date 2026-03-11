from django.urls import re_path

from pipeline.consumers import PipelineProgressConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/pipeline/(?P<job_id>[0-9a-f-]+)/$",
        PipelineProgressConsumer.as_asgi(),
    ),
]
