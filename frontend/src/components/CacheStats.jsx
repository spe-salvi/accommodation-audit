import { useState, useEffect, useCallback } from 'react'
import styles from './CacheStats.module.css'

const ENTITY_LABELS = {
  terms:   { label: 'Terms',   ttl: '1 year' },
  courses: { label: 'Courses', ttl: '30 days' },
  quizzes: { label: 'Quizzes', ttl: '1 day' },
  users:   { label: 'Users',   ttl: '1 year' },
}

export default function CacheStats() {
  const [stats, setStats]         = useState(null)
  const [loading, setLoading]     = useState(true)
  const [invalidating, setInvalidating] = useState(null)
  const [error, setError]         = useState(null)
  const [flashMsg, setFlashMsg]   = useState(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch('/api/cache/stats')
      if (!res.ok) throw new Error('Failed to load cache stats')
      const data = await res.json()
      setStats(data.stats)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function invalidate(entity) {
    setInvalidating(entity)
    try {
      const res = await fetch(`/api/cache/${entity}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to invalidate')
      const data = await res.json()
      setFlashMsg(`Invalidated ${data.count} ${entity} entries.`)
      setTimeout(() => setFlashMsg(null), 3000)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setInvalidating(null)
    }
  }

  if (loading) return (
    <div className={styles.loading}>Loading cache stats…</div>
  )

  if (error) return (
    <div className={styles.error}>{error}</div>
  )

  return (
    <div className={styles.container}>
      {flashMsg && (
        <div className={styles.flash}>{flashMsg}</div>
      )}

      <div className={styles.grid}>
        {stats && Object.entries(stats).map(([entity, info]) => {
          const meta = ENTITY_LABELS[entity] || { label: entity, ttl: '' }
          const hitPct = info.total > 0
            ? Math.round(info.valid / info.total * 100)
            : 0

          return (
            <div className={styles.card} key={entity}>
              <div className={styles.cardHeader}>
                <span className={styles.entityLabel}>{meta.label}</span>
                <span className={styles.ttlBadge}>TTL: {meta.ttl}</span>
              </div>

              <div className={styles.stats}>
                <div className={styles.stat}>
                  <span className={styles.statVal}>{info.total.toLocaleString()}</span>
                  <span className={styles.statKey}>total</span>
                </div>
                <div className={styles.stat}>
                  <span className={`${styles.statVal} ${styles.valid}`}>
                    {info.valid.toLocaleString()}
                  </span>
                  <span className={styles.statKey}>valid</span>
                </div>
                <div className={styles.stat}>
                  <span className={`${styles.statVal} ${info.expired > 0 ? styles.expired : ''}`}>
                    {info.expired.toLocaleString()}
                  </span>
                  <span className={styles.statKey}>expired</span>
                </div>
              </div>

              {info.total > 0 && (
                <div className={styles.barRow}>
                  <div className={styles.miniTrack}>
                    <div
                      className={styles.miniFill}
                      style={{ width: `${hitPct}%` }}
                    />
                  </div>
                  <span className={styles.hitPct}>{hitPct}% valid</span>
                </div>
              )}

              <button
                className={styles.invalidateBtn}
                onClick={() => invalidate(entity)}
                disabled={invalidating === entity || info.total === 0}
              >
                {invalidating === entity ? 'Clearing…' : 'Invalidate'}
              </button>
            </div>
          )
        })}
      </div>

      <button className={styles.refreshBtn} onClick={load}>
        ↻ Refresh
      </button>
    </div>
  )
}
