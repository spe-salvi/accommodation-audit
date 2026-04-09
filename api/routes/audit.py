"""
Audit API routes.

POST /api/audit                — start a new audit job
GET  /api/audit/{job_id}/stream   — SSE progress stream
GET  /api/audit/{job_id}/rows     — completed rows as JSON
GET  /api/audit/{job_id}/download — Excel file download
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from api.jobs import JobStatus, job_store, run_audit_job
from api.models import AuditRequest, AuditRowResponse, JobCreated

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.post("", response_model=JobCreated, status_code=202)
async def start_audit(request: AuditRequest, background_tasks: BackgroundTasks):
    """
    Start a new audit job.

    Validates the scope using the same rules as the frontend form,
    then creates a job and starts the background task.
    """
    has_term   = request.term   is not None
    has_course = request.course is not None
    has_quiz   = request.quiz   is not None
    has_user   = request.user   is not None

    if not has_term and not has_course and not has_quiz and not has_user:
        raise HTTPException(status_code=422,
            detail="Enter at least one scope field.")
    if has_quiz and not has_course:
        raise HTTPException(status_code=422,
            detail="Quiz requires a course.")

    job = job_store.create()

    background_tasks.add_task(
        run_audit_job,
        job,
        request.model_dump(),
    )

    logger.info("Audit job %s created: %s", job.job_id, request.model_dump())
    return JobCreated(job_id=job.job_id)


@router.delete("/{job_id}", status_code=200)
async def abort_audit(job_id: str):
    """
    Request cancellation of a running audit job.

    Sets the job's cancelled flag. The background task checks this
    flag between steps and terminates cleanly on the next check.
    Returns immediately — the SSE stream will emit an error event
    when the job actually stops.
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.PENDING, "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Job cannot be cancelled (status: {job.status})",
        )
    job.cancelled = True
    logger.info("Audit job %s cancellation requested.", job_id)
    return {"job_id": job_id, "message": "Cancellation requested."}


@router.get("/{job_id}/stream")
async def stream_progress(job_id: str):
    """
    SSE stream of audit progress events.

    Streams JSON events until the job reaches a terminal state
    (complete or error). The client should close the connection
    on receiving a complete or error event.
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        # Wait briefly for the background task to start
        for _ in range(20):
            if job.status != JobStatus.PENDING:
                break
            await asyncio.sleep(0.1)

        # If the job already reached a terminal state before the stream
        # connected (e.g. immediate validation error in the background task),
        # drain any queued events first then synthesize the terminal event.
        if job.status in (JobStatus.COMPLETE, JobStatus.ERROR):
            # Drain any events already in the queue
            while not job.events.empty():
                try:
                    event = job.events.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("complete", "error"):
                        return
                except Exception:
                    break
            # Queue was empty but job is terminal — synthesize the event
            if job.status == JobStatus.ERROR:
                msg = job.error or "An unexpected error occurred."
                yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'complete', 'row_count': len(job.rows), 'elapsed': job.elapsed, 'metrics': job.metrics})}\n\n"
            return

        while True:
            try:
                event = await asyncio.wait_for(job.events.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("complete", "error"):
                    break
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                if job.status in (JobStatus.COMPLETE, JobStatus.ERROR):
                    # Synthesize terminal event if queue was drained without it
                    if job.status == JobStatus.ERROR:
                        msg = job.error or "An unexpected error occurred."
                        yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                    elif job.status == JobStatus.COMPLETE:
                        yield f"data: {json.dumps({'type': 'complete', 'row_count': len(job.rows), 'elapsed': job.elapsed, 'metrics': job.metrics})}\n\n"
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering on Render
        },
    )


@router.get("/{job_id}/rows", response_model=list[AuditRowResponse])
async def get_rows(job_id: str):
    """Return the completed audit rows as JSON."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETE:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not complete (status: {job.status})",
        )
    return job.rows


@router.get("/{job_id}/download")
async def download_report(job_id: str):
    """Generate and stream an Excel report for a completed audit job."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETE:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not complete (status: {job.status})",
        )

    # Write xlsx to an in-memory buffer
    buffer = io.BytesIO()
    _write_xlsx_to_buffer(job.rows, buffer)
    buffer.seek(0)

    filename = f"audit_{job_id[:8]}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _write_xlsx_to_buffer(rows: list[dict], buffer: io.BytesIO) -> None:
    """Write audit rows to an Excel workbook in the given buffer."""
    import pandas as pd

    if not rows:
        df = pd.DataFrame()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Audit")
        return

    df = pd.DataFrame(rows)

    # Expand details dict columns if present
    if "details" in df.columns:
        details_df = pd.json_normalize(df["details"])
        df = df.drop(columns=["details"]).join(details_df)

    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Audit")

        workbook  = writer.book
        worksheet = writer.sheets["Audit"]

        # Conditional formatting — green for True, yellow for False
        green  = workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#276221"})
        yellow = workbook.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500"})

        if "has_accommodation" in df.columns:
            col_idx = df.columns.get_loc("has_accommodation")
            worksheet.conditional_format(
                1, col_idx, len(df), col_idx,
                {"type": "cell", "criteria": "==", "value": True,  "format": green},
            )
            worksheet.conditional_format(
                1, col_idx, len(df), col_idx,
                {"type": "cell", "criteria": "==", "value": False, "format": yellow},
            )

        # Auto-fit columns (approximate)
        for i, col in enumerate(df.columns):
            max_len = max(len(str(col)), df[col].astype(str).str.len().max() or 0)
            worksheet.set_column(i, i, min(max_len + 2, 40))
