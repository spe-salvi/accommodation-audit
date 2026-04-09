import { useState } from 'react'
import styles from './AuditForm.module.css'

const ENGINES = [
  { value: 'all',     label: 'Both engines' },
  { value: 'classic', label: 'Classic only' },
  { value: 'new',     label: 'New only' },
]

const TYPES = [
  { value: 'extra_time',    label: 'Extra time' },
  { value: 'extra_attempt', label: 'Extra attempts' },
  { value: 'spell_check',   label: 'Spell check' },
]

export default function AuditForm({ onSubmit, disabled }) {
  const [scope, setScope] = useState({
    term: '', course: '', quiz: '', user: '',
  })
  const [engine, setEngine]  = useState('all')
  const [types, setTypes]    = useState(['extra_time', 'extra_attempt', 'spell_check'])
  const [error, setError]    = useState('')

  function toggleType(value) {
    setTypes(prev =>
      prev.includes(value) ? prev.filter(t => t !== value) : [...prev, value]
    )
  }

  function validate() {
    const filled = Object.entries(scope).filter(([, v]) => v.trim())
    const scopeFields = filled.filter(([k]) => k !== 'user')

    if (!scope.user.trim() && scopeFields.length === 0)
      return 'Enter at least one scope field (term, course, quiz, or user).'
    if (!scope.user.trim() && scopeFields.length > 1)
      return 'Use only one of term, course, or quiz at a time (without user).'
    if (scope.user.trim() && scopeFields.length > 1)
      return 'Combine user with at most one other field.'
    if (scope.quiz.trim() && !scope.course.trim())
      return 'Quiz search requires a course.'
    if (types.length === 0)
      return 'Select at least one accommodation type.'
    return ''
  }

  function handleSubmit(e) {
    e.preventDefault()
    const err = validate()
    if (err) { setError(err); return }
    setError('')

    const payload = { engine, types }
    if (scope.term.trim())   payload.term   = scope.term.trim()
    if (scope.course.trim()) payload.course = scope.course.trim()
    if (scope.quiz.trim())   payload.quiz   = scope.quiz.trim()
    if (scope.user.trim())   payload.user   = scope.user.trim()

    onSubmit(payload)
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <div className={styles.grid}>
        {[
          { key: 'term',   label: 'Term',   placeholder: '117  or  "Spring 2026"' },
          { key: 'course', label: 'Course', placeholder: '12977  or  "CHM-115"' },
          { key: 'quiz',   label: 'Quiz',   placeholder: '48379  or  "Midterm"' },
          { key: 'user',   label: 'User',   placeholder: '99118  or  "McCarthy"' },
        ].map(({ key, label, placeholder }) => (
          <div className={styles.field} key={key}>
            <label className={styles.label} htmlFor={key}>{label}</label>
            <input
              id={key}
              className={styles.input}
              type="text"
              placeholder={placeholder}
              value={scope[key]}
              onChange={e => setScope(s => ({ ...s, [key]: e.target.value }))}
              disabled={disabled}
              autoComplete="off"
            />
          </div>
        ))}
      </div>

      <div className={styles.options}>
        <fieldset className={styles.fieldset}>
          <legend className={styles.legend}>Engine</legend>
          <div className={styles.radioGroup}>
            {ENGINES.map(({ value, label }) => (
              <label key={value} className={styles.radio}>
                <input
                  type="radio"
                  name="engine"
                  value={value}
                  checked={engine === value}
                  onChange={() => setEngine(value)}
                  disabled={disabled}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className={styles.fieldset}>
          <legend className={styles.legend}>Accommodation types</legend>
          <div className={styles.checkGroup}>
            {TYPES.map(({ value, label }) => (
              <label key={value} className={styles.check}>
                <input
                  type="checkbox"
                  checked={types.includes(value)}
                  onChange={() => toggleType(value)}
                  disabled={disabled}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      {error && <p className={styles.error}>{error}</p>}

      <button className={styles.submit} type="submit" disabled={disabled}>
        {disabled ? 'Running…' : 'Run audit'}
      </button>
    </form>
  )
}
