/**
 * useAudit — manages audit job lifecycle.
 *
 * Handles:
 * - POST /api/audit to start a job
 * - SSE stream for real-time progress
 * - GET /api/audit/{id}/rows for completed rows
 * - Download link for Excel report
 */

import { useState, useRef, useCallback } from 'react'

export const JOB_STATE = {
  IDLE:     'idle',
  RUNNING:  'running',
  COMPLETE: 'complete',
  ERROR:    'error',
}

export function useAudit() {
  const [state, setState] = useState(JOB_STATE.IDLE)
  const [jobId, setJobId] = useState(null)
  const [progress, setProgress] = useState({ completed: 0, total: 0, pct: 0, desc: '' })
  const [metrics, setMetrics] = useState(null)
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)
  const [phase, setPhase] = useState('')  // 'auditing' | 'enriching' | ''
  const esRef = useRef(null)

  const reset = useCallback(() => {
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    setState(JOB_STATE.IDLE)
    setJobId(null)
    setProgress({ completed: 0, total: 0, pct: 0, desc: '' })
    setMetrics(null)
    setRows([])
    setError(null)
    setPhase('')
  }, [])

  const startAudit = useCallback(async (formData, setFormError) => {
    reset()
    setState(JOB_STATE.RUNNING)
    setPhase('auditing')

    try {
      // Start the job
      const res = await fetch('/api/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      })
      if (!res.ok) {
        const err = await res.json()
        const msg = err.detail || 'Failed to start audit'
        // 422 validation errors belong in the form, not the progress card
        if (res.status === 422 && setFormError) {
          setFormError(msg)
          setState(JOB_STATE.IDLE)
          setPhase('')
          return
        }
        throw new Error(msg)
      }
      const { job_id } = await res.json()
      setJobId(job_id)

      // Open SSE stream
      const es = new EventSource(`/api/audit/${job_id}/stream`)
      esRef.current = es

      es.onmessage = (e) => {
        const event = JSON.parse(e.data)

        if (event.type === 'start') {
          setProgress({ completed: 0, total: event.total, pct: 0, desc: 'Starting...' })
        }
        else if (event.type === 'progress') {
          setProgress({
            completed: event.completed,
            total: event.total,
            pct: event.pct,
            desc: event.desc || '',
          })
        }
        else if (event.type === 'enrich') {
          setPhase('enriching')
          setProgress(p => ({ ...p, desc: event.message }))
        }
        else if (event.type === 'complete') {
          es.close()
          esRef.current = null
          setMetrics(event.metrics)
          setProgress(p => ({ ...p, pct: 100, desc: 'Complete' }))
          setPhase('')
          // Fetch rows
          fetch(`/api/audit/${job_id}/rows`)
            .then(r => r.json())
            .then(data => {
              setRows(data)
              setState(JOB_STATE.COMPLETE)
            })
            .catch(err => {
              setError(err.message)
              setState(JOB_STATE.ERROR)
            })
        }
        else if (event.type === 'error') {
          es.close()
          esRef.current = null
          setError(event.message)
          setState(JOB_STATE.ERROR)
          setPhase('')
        }
      }

      es.onerror = () => {
        if (esRef.current) {
          esRef.current.close()
          esRef.current = null
        }
        setError('Connection to server lost.')
        setState(JOB_STATE.ERROR)
        setPhase('')
      }

    } catch (err) {
      setError(err.message)
      setState(JOB_STATE.ERROR)
      setPhase('')
    }
  }, [reset])

  const abortAudit = useCallback(async () => {
    if (!jobId) return
    try {
      await fetch(`/api/audit/${jobId}`, { method: 'DELETE' })
    } catch (e) {
      // ignore — SSE will surface the cancellation event
    }
  }, [jobId])

  const downloadUrl = jobId ? `/api/audit/${jobId}/download` : null

  return {
    state, jobId, progress, metrics, rows, error, phase,
    startAudit, abortAudit, reset, downloadUrl,
  }
}
