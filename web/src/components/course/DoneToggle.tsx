import { useState } from 'react'
import { setDone } from '../../api/client'
import type { AssignmentSummary } from '../../api/types'
import { toApiError } from '../../lib/useApi'

/** The Done checkbox on class-page homework. Other assignments take their status from submissions. */
export function DoneToggle({ assignment, onChange }: { assignment: AssignmentSummary; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function toggle(done: boolean) {
    setBusy(true)
    setError(null)
    try {
      await setDone(assignment.id, done)
      onChange()
    } catch (err) {
      setError(toApiError(err).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="done-toggle">
      <label className="check">
        <input
          id={`done-${assignment.id}`}
          type="checkbox"
          aria-label={`Done: ${assignment.title}`}
          checked={assignment.status === 'done'}
          disabled={busy}
          onChange={(e) => void toggle(e.target.checked)}
        />
        Done
      </label>
      {error && <span className="bad-text">{error}</span>}
    </span>
  )
}
