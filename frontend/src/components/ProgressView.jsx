import styles from './ProgressView.module.css'

function fmt(seconds) {
  const s = Math.round(seconds || 0)
  return s >= 60 ? `${Math.floor(s/60)}m ${s%60}s` : `${s}s`
}

function MetricPill({ label, value }) {
  return (
    <div className={styles.pill}>
      <span className={styles.pillValue}>{value}</span>
      <span className={styles.pillLabel}>{label}</span>
    </div>
  )
}

export default function ProgressView({ progress, phase, metrics, error, onReset }) {
  const isEnriching = phase === 'enriching'
  const isComplete  = !!metrics
  const pCacheTotal = (metrics?.persistent_cache_hits || 0) + (metrics?.persistent_cache_misses || 0)
  const pCacheRate  = pCacheTotal ? Math.round(metrics.persistent_cache_hits / pCacheTotal * 100) : 0

  if (error) {
    return (
      <div className={styles.container}>
        <div className={styles.errorBox}>
          <span className={styles.errorIcon}>✕</span>
          <div>
            <p className={styles.errorTitle}>Audit failed</p>
            <p className={styles.errorMsg}>{error}</p>
          </div>
        </div>
        <button className={styles.resetBtn} onClick={onReset}>
          ← Start over
        </button>
      </div>
    )
  }

  return (
    <div className={styles.container}>
      {/* Phase label */}
      <p className={styles.phase}>
        {isComplete ? 'Audit complete' : isEnriching ? 'Enriching…' : 'Auditing…'}
      </p>

      {/* Description */}
      {progress.desc && (
        <p className={styles.desc}>{progress.desc}</p>
      )}

      {/* Progress bar */}
      <div className={styles.barTrack}>
        <div
          className={`${styles.barFill} ${isComplete ? styles.barDone : ''}`}
          style={{ width: `${Math.max(progress.pct || 0, isComplete ? 100 : 0)}%` }}
        />
      </div>

      <div className={styles.barMeta}>
        <span className={styles.mono}>
          {progress.completed.toLocaleString()} / {progress.total.toLocaleString()}
        </span>
        <span className={styles.mono}>{progress.pct ?? 0}%</span>
      </div>

      {/* Metrics summary — shown when complete */}
      {isComplete && metrics && (
        <div className={styles.metrics}>
          <MetricPill label="rows"         value={metrics.row_count?.toLocaleString()} />
          <MetricPill label="total time"   value={fmt(metrics.total_elapsed)} />
          <MetricPill label="API calls"    value={metrics.api_requests_made?.toLocaleString()} />
          <MetricPill label="P-cache"      value={`${pCacheRate}%`} />
          {metrics.api_retries_fired > 0 && (
            <MetricPill label="retries"    value={metrics.api_retries_fired} />
          )}
        </div>
      )}
    </div>
  )
}
