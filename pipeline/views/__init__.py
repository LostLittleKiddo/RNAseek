"""Pipeline views package — re-exports all views for backward compatibility."""

from pipeline.views.pages import (          # noqa: F401
    CoreHubView,
    HomeView,
    NewSubmissionView,
    ProcessingView,
    TutorialsView,
    WorkspacesView,
)

from pipeline.views.api import (            # noqa: F401
    ChunkUploadView,
    CorePipelineView,
    CreateSubmissionView,
    DeleteSubmissionView,
    FileAssetDeleteView,
    FileDownloadView,
    JobStatusView,
    ModuleRunView,
    SessionAssetsView,
    TCGACohortsView,
    TusdHookView,
)

from pipeline.views.tus_webhook import (    # noqa: F401
    TusWebhookView,
    TusAssetLookupView,
)
