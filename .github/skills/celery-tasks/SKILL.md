---
description: "USE when implementing Celery tasks, adding pipeline steps with progress tracking, or debugging task execution and WebSocket updates."
---

# Skill: Celery Task with Progress Tracking

How to implement a Celery task with real-time WebSocket progress updates in RNAseek.

## Architecture overview

- Celery tasks run in worker processes (or synchronously when `CELERY_EAGER=1`)
- Each task has an `AnalysisJob` row that tracks status and step progress
- `step_progress` is a JSONField pushed to the frontend via Django Channels WebSocket
- The frontend processing page polls `/api/jobs/<id>/` every 3s and listens on WebSocket

## AnalysisJob status lifecycle

```
PENDING → RUNNING → SUCCESS
                  → FAILED
```

## step_progress JSON shape

```json
{
    "pipeline_steps": ["fastqc", "trimmomatic", "hisat2", "featurecounts", "multiqc", "deseq2"],
    "current_step": "hisat2",
    "completed_steps": ["fastqc", "trimmomatic"],
    "failed_step": null
}
```

## Pattern: Core pipeline task (existing)

The `run_core_pipeline` task in `pipeline/tasks/core.py`:

```python
@shared_task(bind=True)
def run_core_pipeline(self, session_id, submission_id):
    job = AnalysisJob.objects.get(job_id=self.request.id)  # job_id == Celery task ID
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        # ... route to track function ...
        # On success:
        job.status = AnalysisJob.Status.SUCCESS
        job.result_payload = {"message": "...", **result}
        job.save(update_fields=["status", "result_payload", "step_progress"])
        _emit_progress(job)

    except Exception as exc:
        # On failure:
        progress = job.step_progress or {}
        progress["failed_step"] = progress.get("current_step")
        progress["current_step"] = None
        job.step_progress = progress
        job.status = AnalysisJob.Status.FAILED
        job.result_payload = {"error": str(exc)}
        job.save(update_fields=["status", "result_payload", "step_progress"])
        _emit_progress(job)
        raise
```

## Pattern: Step tracking within a track/module function

```python
from pipeline.tasks._helpers import _update_step, _emit_progress, _run, _q

def _route_my_track(submission, job):
    # Before each step — marks as "running", emits WebSocket update
    _update_step(job, "my_step")

    # Do the work
    _run(f"tool --input {_q(input_path)} --output {_q(output_path)}", cwd=work_dir)

    # After each step — marks as "completed", emits WebSocket update
    _update_step(job, "my_step", completed=True)
```

## Pattern: Shell command execution

```python
from pipeline.tasks._helpers import _run, _q

# Simple command
_run(f"fastqc -o {_q(qc_dir)} -t 4 {_q(fastq_path)}")

# Piped command (auto-prepends `set -o pipefail`)
_run(f"hisat2 -x {_q(index)} -U {_q(reads)} | samtools sort -o {_q(bam_out)}")

# With working directory
_run("multiqc . -o qc/ --force", cwd=work_dir)
```

Key rules:
- `_run()` raises `RuntimeError` on non-zero exit, which triggers the except block in the task
- `_q()` uses `shlex.quote()` to prevent shell injection
- Piped commands get `set -o pipefail` prepended automatically
- All commands run under `/bin/bash`

## Pattern: Parallel sample processing

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from pipeline.tasks._constants import _PARALLEL_SAMPLES

def _process_one_sample(sample_path):
    _run(f"tool {_q(sample_path)}")
    return result_path

with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
    futures = [pool.submit(_process_one_sample, p) for p in sample_paths]
    results = [fut.result() for fut in as_completed(futures)]
```

Note: Do NOT use ThreadPoolExecutor for R code (rpy2 is not thread-safe).

## Pattern: Tier 2 module dispatch

The `run_tier2_module` task in `core.py` receives `module_name` and calls the appropriate runner.

### Currently implemented modules (4 of 12):

| Module name | Dispatch function | File |
|---|---|---|
| `WGCNA` | `_dispatch_wgcna()` | `_module_wgcna.py` |
| `SPLICING` | `_dispatch_splicing()` | `_module_alt_splicing.py` |
| `RNA_EDITING` | `_dispatch_rna_editing()` | `_module_rna_editing.py` |
| `TIME_SERIES` | `_dispatch_timeseries()` | `_module_timeseries.py` |

The remaining 8 modules (PATHWAY, NETWORKS, LIT_MINING, SURVIVAL, TCGA, BIOMARKER, MOFA, DIABLO) return placeholder payloads.

### To add a new module:

1. Create `_run_my_module(submission, job, params)` in `pipeline/tasks/_module_my_module.py`
2. Add dispatch branch in `run_tier2_module()`:
   ```python
   if module_name == "my_module":
       result = _run_my_module(submission, job, params)
   ```

## Environment configuration

| Variable | Value | Meaning |
|---|---|---|
| `CELERY_EAGER=1` | Default in dev | Tasks run synchronously in the web process |
| `CELERY_EAGER=0` | Production | Tasks dispatched to Celery workers via Redis |
| `_CPU_COUNT` | `os.cpu_count()` | Total available CPUs |
| `_TOOL_THREADS` | `max(2, _CPU_COUNT // 2)` | Threads per bioinformatics tool |
| `_PARALLEL_SAMPLES` | `max(1, _CPU_COUNT // _TOOL_THREADS)` | Concurrent sample processing |

## WebSocket emit

`_emit_progress(job)` sends current state to the `pipeline_{job_id}` channel group. The consumer in `pipeline/consumers.py` forwards it to connected browsers. It silently no-ops if:
- No channel layer is configured (e.g., in tests without Redis)
- The WebSocket group has no subscribers

## Testing tasks

```python
from unittest.mock import patch, MagicMock

# Mock task dispatch in view tests
@patch("pipeline.tasks.core.run_core_pipeline.apply_async")
def test_pipeline_dispatch(self, mock_apply):
    mock_apply.return_value = MagicMock(id="fake-task-id")
    # ... trigger view ...
    mock_apply.assert_called_once()

# Mock _run to avoid requiring real binaries
@patch("pipeline.tasks._helpers._run")
def test_track_steps(self, mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    # ... call track function ...
```
