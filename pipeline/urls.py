from django.urls import path

from pipeline.views import (
    ChunkUploadView,
    CoreHubView,
    CorePipelineView,
    CreateSubmissionView,
    DeleteSubmissionView,
    FileAssetDeleteView,
    FileDownloadView,
    HomeView,
    JobStatusView,
    ModuleRunView,
    NewSubmissionView,
    ProcessingView,
    SessionAssetsView,
    TusdHookView,
    TutorialsView,
    WorkspacesView,
)

urlpatterns = [
    # Pages
    path("", HomeView.as_view(), name="home"),
    path("tutorials/", TutorialsView.as_view(), name="tutorials"),
    path("workspaces/", WorkspacesView.as_view(), name="workspaces"),
    path("analysis_submission/new/", NewSubmissionView.as_view(), name="new_submission"),
    path("processing/<uuid:job_id>/", ProcessingView.as_view(), name="processing"),
    path("hub/<uuid:job_id>/", CoreHubView.as_view(), name="core_hub"),

    # API endpoints
    path("api/submission/create", CreateSubmissionView.as_view(), name="create_submission"),
    path("api/submission/delete", DeleteSubmissionView.as_view(), name="delete_submission"),
    path("api/upload/chunk", ChunkUploadView.as_view(), name="upload_chunk"),
    path("api/pipeline/core", CorePipelineView.as_view(), name="pipeline_core"),
    path("api/jobs/<uuid:job_id>/", JobStatusView.as_view(), name="job_status"),
    path("api/files/<uuid:asset_id>/", FileAssetDeleteView.as_view(), name="file_asset_delete"),
    path("api/session/assets", SessionAssetsView.as_view(), name="session_assets"),
    path("api/download/<uuid:asset_id>", FileDownloadView.as_view(), name="file_download"),
    path("api/submissions/<uuid:submission_id>/modules/<str:module_name>/run", ModuleRunView.as_view(), name="module_run"),
    path("api/tusd-hooks/", TusdHookView.as_view(), name="tusd_hooks"),
]
