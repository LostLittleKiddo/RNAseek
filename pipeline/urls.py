from django.urls import path

from pipeline.views import (
    AdvancedView,
    ChunkUploadView,
    CoreHubView,
    CorePipelineView,
    HomeView,
    JobStatusView,
    NewSubmissionView,
    ProcessingView,
    SessionAssetsView,
    TutorialsView,
    WorkspacesView,
)

urlpatterns = [
    # Pages
    path("", HomeView.as_view(), name="home"),
    path("tutorials/", TutorialsView.as_view(), name="tutorials"),
    path("workspaces/", WorkspacesView.as_view(), name="workspaces"),
    path("new/", NewSubmissionView.as_view(), name="new_submission"),
    path("processing/<uuid:job_id>/", ProcessingView.as_view(), name="processing"),
    path("hub/<uuid:job_id>/", CoreHubView.as_view(), name="core_hub"),
    path("advanced/<uuid:job_id>/", AdvancedView.as_view(), name="advanced"),

    # API endpoints
    path("api/upload/chunk", ChunkUploadView.as_view(), name="upload_chunk"),
    path("api/pipeline/core", CorePipelineView.as_view(), name="pipeline_core"),
    path("api/jobs/<uuid:job_id>/", JobStatusView.as_view(), name="job_status"),
    path("api/session/assets", SessionAssetsView.as_view(), name="session_assets"),
]
