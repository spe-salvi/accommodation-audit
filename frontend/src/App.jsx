import { useState } from 'react'
import AuditForm    from './components/AuditForm.jsx'
import ProgressView from './components/ProgressView.jsx'
import ResultsTable from './components/ResultsTable.jsx'
import CacheStats   from './components/CacheStats.jsx'
import { useAudit, JOB_STATE } from './hooks/useAudit.js'
import styles from './App.module.css'

const TABS = [
  { id: 'audit', label: 'Audit' },
  { id: 'cache', label: 'Cache' },
]

export default function App() {
  const [tab, setTab] = useState('audit')
  const audit = useAudit()

  const isRunning  = audit.state === JOB_STATE.RUNNING
  const isComplete = audit.state === JOB_STATE.COMPLETE
  const isError    = audit.state === JOB_STATE.ERROR
  const showForm   = audit.state === JOB_STATE.IDLE
  const showProgress = isRunning || isComplete || isError

  return (
    <div className={styles.app}>
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div className={styles.brand}>
            <h1 className={styles.title}>Accommodation Audit</h1>
            <span className={styles.subtitle}>Canvas LMS · Franciscan University</span>
          </div>
          <nav className={styles.nav}>
            {TABS.map(t => (
              <button
                key={t.id}
                className={`${styles.navBtn} ${tab === t.id ? styles.navActive : ''}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* Main */}
      <main className={styles.main}>
        {tab === 'audit' && (
          <div className={styles.auditLayout}>

            {/* Left panel — form / progress */}
            <aside className={styles.sidebar}>
              <div className={styles.card}>
                <h2 className={styles.cardTitle}>Configure audit</h2>
                <AuditForm
                  onSubmit={audit.startAudit}
                  disabled={isRunning}
                />
              </div>

              {showProgress && (
                <div className={styles.card}>
                  <h2 className={styles.cardTitle}>
                    {isComplete ? 'Summary' : 'Progress'}
                  </h2>
                  <ProgressView
                    progress={audit.progress}
                    phase={audit.phase}
                    metrics={audit.metrics}
                    error={audit.error}
                    onReset={audit.reset}
                  />
                </div>
              )}
            </aside>

            {/* Right panel — results table */}
            <section className={styles.results}>
              {isComplete ? (
                <ResultsTable
                  rows={audit.rows}
                  downloadUrl={audit.downloadUrl}
                  onReset={audit.reset}
                />
              ) : (
                <div className={styles.emptyResults}>
                  {isRunning ? (
                    <>
                      <div className={styles.spinner} />
                      <p>Audit running…</p>
                    </>
                  ) : (
                    <>
                      <div className={styles.emptyIcon}>⊙</div>
                      <p>Results will appear here when the audit completes.</p>
                    </>
                  )}
                </div>
              )}
            </section>

          </div>
        )}

        {tab === 'cache' && (
          <div className={styles.cachePage}>
            <div className={styles.card}>
              <h2 className={styles.cardTitle}>Persistent cache</h2>
              <p className={styles.cardDesc}>
                File-backed TTL cache stored under <code>.cache/</code>.
                Invalidating an entity forces the next audit to re-fetch
                from Canvas while preserving existing data on disk.
              </p>
              <CacheStats />
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
