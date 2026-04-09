import { useState, useMemo } from 'react'
import styles from './ResultsTable.module.css'

const COLUMNS = [
  { key: 'has_accommodation', label: 'Status',      width: 90 },
  { key: 'user_name',         label: 'Student',      width: 180 },
  { key: 'sis_user_id',       label: 'SIS ID',       width: 100 },
  { key: 'term_name',         label: 'Term',         width: 140 },
  { key: 'course_name',       label: 'Course',       width: 200 },
  { key: 'course_code',       label: 'Code',         width: 110 },
  { key: 'quiz_title',        label: 'Quiz',         width: 180 },
  { key: 'engine',            label: 'Engine',       width: 80 },
  { key: 'accommodation_type',label: 'Type',         width: 130 },
  { key: 'completed',         label: 'Completed',    width: 90 },
  { key: 'attempts_left',     label: 'Attempts left',width: 110 },
]

const PAGE_SIZE = 100

function StatusBadge({ value }) {
  if (value === true)  return <span className={`${styles.badge} ${styles.yes}`}>✓ Yes</span>
  if (value === false) return <span className={`${styles.badge} ${styles.no}`}>✗ No</span>
  return <span className={styles.dash}>—</span>
}

function Cell({ col, value }) {
  if (col.key === 'has_accommodation') return <StatusBadge value={value} />
  if (col.key === 'completed') {
    if (value === true)  return <span className={styles.dim}>Yes</span>
    if (value === false) return <span className={styles.dim}>No</span>
    return <span className={styles.dash}>—</span>
  }
  if (value === null || value === undefined || value === '') {
    return <span className={styles.dash}>—</span>
  }
  const isMono = ['sis_user_id','course_code','engine','accommodation_type'].includes(col.key)
  return (
    <span className={isMono ? styles.mono : undefined} title={String(value)}>
      {String(value)}
    </span>
  )
}

export default function ResultsTable({ rows, downloadUrl, onReset }) {
  const [filter, setFilter]     = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [page, setPage]         = useState(0)

  const types = useMemo(() => {
    const s = new Set(rows.map(r => r.accommodation_type).filter(Boolean))
    return ['all', ...Array.from(s).sort()]
  }, [rows])

  const filtered = useMemo(() => {
    const q = filter.toLowerCase()
    return rows.filter(r => {
      if (typeFilter !== 'all' && r.accommodation_type !== typeFilter) return false
      if (statusFilter === 'yes' && !r.has_accommodation)  return false
      if (statusFilter === 'no'  &&  r.has_accommodation)  return false
      if (!q) return true
      return [r.user_name, r.sis_user_id, r.course_name, r.course_code,
              r.quiz_title, r.term_name].some(v => v?.toLowerCase().includes(q))
    })
  }, [rows, filter, typeFilter, statusFilter])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const pageRows   = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function handleFilterChange(val) { setFilter(val); setPage(0) }
  function handleTypeChange(val)   { setTypeFilter(val); setPage(0) }
  function handleStatusChange(val) { setStatusFilter(val); setPage(0) }

  return (
    <div className={styles.container}>
      {/* Toolbar */}
      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          <input
            className={styles.search}
            type="text"
            placeholder="Filter by name, course, quiz…"
            value={filter}
            onChange={e => handleFilterChange(e.target.value)}
          />
          <select
            className={styles.select}
            value={typeFilter}
            onChange={e => handleTypeChange(e.target.value)}
          >
            {types.map(t => (
              <option key={t} value={t}>
                {t === 'all' ? 'All types' : t.replace('_', ' ')}
              </option>
            ))}
          </select>
          <select
            className={styles.select}
            value={statusFilter}
            onChange={e => handleStatusChange(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="yes">Has accommodation</option>
            <option value="no">Missing accommodation</option>
          </select>
        </div>
        <div className={styles.toolbarRight}>
          <span className={styles.count}>
            {filtered.length.toLocaleString()} of {rows.length.toLocaleString()} rows
          </span>
          {downloadUrl && (
            <a className={styles.download} href={downloadUrl} download>
              ↓ Download Excel
            </a>
          )}
          <button className={styles.newAudit} onClick={onReset}>
            New audit
          </button>
        </div>
      </div>

      {/* Table */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  className={styles.th}
                  style={{ minWidth: col.width }}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length} className={styles.empty}>
                  No rows match the current filters.
                </td>
              </tr>
            ) : pageRows.map((row, i) => (
              <tr
                key={`${row.user_id}-${row.quiz_id}-${row.accommodation_type}-${i}`}
                className={`${styles.tr} ${row.has_accommodation ? styles.trYes : styles.trNo}`}
              >
                {COLUMNS.map(col => (
                  <td key={col.key} className={styles.td}>
                    <Cell col={col} value={row[col.key]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className={styles.pagination}>
          <button
            className={styles.pageBtn}
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
          >←</button>
          <span className={styles.pageInfo}>
            Page {page + 1} of {totalPages}
          </span>
          <button
            className={styles.pageBtn}
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page === totalPages - 1}
          >→</button>
        </div>
      )}
    </div>
  )
}
