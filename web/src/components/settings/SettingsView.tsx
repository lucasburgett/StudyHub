import { useState, type FormEvent } from 'react'
import { checkConnection, endpoints, saveSettings } from '../../api/client'
import type { CheckResult, SettingField, SettingGroup, Source, SourceStatus } from '../../api/types'
import { useApp } from '../../lib/appContext'
import { formatAgo, plural } from '../../lib/format'
import { SOURCES } from '../../lib/labels'
import { toApiError, useApi } from '../../lib/useApi'
import { ErrorState, Loading, SourceDot, Spinner } from '../ui'

const isSource = (id: string): id is Source => (SOURCES as string[]).includes(id)

type Drafts = Record<string, string>

/** The value a field shows before any edits. Secrets have no visible value. */
function originalValue(field: SettingField): string | null {
  if (field.kind === 'secret') return field.is_set ? null : ''
  return field.value ?? ''
}

function SourceLine({ status }: { status: SourceStatus | undefined }) {
  if (!status?.configured) return <span className="muted">Not connected</span>
  if (status.running) return <span className="muted">Syncing…</span>
  const run = status.last_run
  if (!run) return <span className="muted">Not synced yet</span>
  if (run.status === 'error') return <span className="bad-text">Last sync failed {formatAgo(run.started_at)}</span>
  return (
    <span className="muted">
      Synced {formatAgo(run.finished_at ?? run.started_at)} · {plural(run.items_changed, 'item')} changed
    </span>
  )
}

function SecretInput({
  field,
  draft,
  id,
  describedBy,
  onChange,
  onReset,
}: {
  field: SettingField
  draft: string | undefined
  id: string
  describedBy: string | undefined
  onChange: (value: string) => void
  onReset: () => void
}) {
  const [replacing, setReplacing] = useState(false)
  if (field.locked) {
    return <input id={id} type="password" value="••••••••" disabled aria-describedby={describedBy} />
  }
  if (field.is_set && draft === '') {
    return (
      <div className="secret-saved">
        <span>Will be removed when you save.</span>
        <button type="button" className="btn small ghost" onClick={onReset}>
          Undo
        </button>
      </div>
    )
  }
  if (field.is_set && !replacing && draft === undefined) {
    return (
      <div className="secret-saved">
        <span className="mono">Saved {field.hint}</span>
        <button type="button" className="btn small" onClick={() => setReplacing(true)}>
          Replace
        </button>
        <button type="button" className="btn small ghost" onClick={() => onChange('')}>
          Remove
        </button>
      </div>
    )
  }
  return (
    <input
      id={id}
      type="password"
      autoComplete="new-password"
      spellCheck={false}
      value={draft ?? ''}
      placeholder={field.is_set ? 'Paste the new value' : field.placeholder || 'Paste it here'}
      aria-describedby={describedBy}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

function FieldControl({
  field,
  draft,
  onChange,
  onReset,
}: {
  field: SettingField
  draft: string | undefined
  onChange: (value: string) => void
  onReset: () => void
}) {
  const id = `setting-${field.key}`
  const helpId = field.help ? `${id}-help` : undefined
  const value = draft ?? field.value ?? ''
  const common = { id, disabled: field.locked, 'aria-describedby': helpId }

  let control
  switch (field.kind) {
    case 'secret':
      control = (
        <SecretInput field={field} draft={draft} id={id} describedBy={helpId} onChange={onChange} onReset={onReset} />
      )
      break
    case 'lines':
      control = (
        <textarea
          {...common}
          rows={3}
          spellCheck={false}
          value={value}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )
      break
    case 'select':
      control = (
        <select {...common} value={value} onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )
      break
    case 'bool':
      control = (
        <input
          {...common}
          type="checkbox"
          checked={value === 'true'}
          onChange={(e) => onChange(e.target.checked ? 'true' : 'false')}
        />
      )
      break
    default:
      control = (
        <input
          {...common}
          type="text"
          spellCheck={false}
          autoComplete="off"
          value={value}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }

  return (
    <div className={`setting-field kind-${field.kind}`}>
      <label htmlFor={id}>{field.label}</label>
      {control}
      {field.locked && <p className="field-help">Set by an environment variable, which overrides this file.</p>}
      {field.help && (
        <p id={helpId} className="field-help">
          {field.help}
        </p>
      )}
    </div>
  )
}

function GroupCard({ group, onSaved }: { group: SettingGroup; onSaved: () => void }) {
  const { sync } = useApp()
  const [drafts, setDrafts] = useState<Drafts>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)
  const [check, setCheck] = useState<CheckResult | 'running' | null>(null)

  const source = isSource(group.id) ? group.id : null
  const status = source ? sync.status?.sources.find((s) => s.source === source) : undefined
  const warnings = status?.last_run?.warnings ?? []
  const dirty = Object.keys(drafts).length > 0

  const setDraft = (field: SettingField, value: string) => {
    setJustSaved(false)
    setCheck(null)
    setDrafts((d) => {
      const next = { ...d }
      if (value === originalValue(field)) delete next[field.key]
      else next[field.key] = value
      return next
    })
  }
  const resetDraft = (key: string) =>
    setDrafts((d) => {
      const next = { ...d }
      delete next[key]
      return next
    })

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await saveSettings(drafts)
      setDrafts({})
      setJustSaved(true)
      onSaved()
      sync.refresh()
    } catch (err) {
      setError(toApiError(err).message)
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setCheck('running')
    try {
      setCheck(await checkConnection(group.id))
    } catch (err) {
      setCheck({ ok: false, message: toApiError(err).message })
    }
  }

  const headingId = `settings-${group.id}`
  return (
    <section className="card setting-group" aria-labelledby={headingId}>
      <header>
        <h2 id={headingId}>
          {source && <SourceDot source={source} />}
          {group.title}
        </h2>
        {source && <SourceLine status={status} />}
      </header>
      {group.intro && <p className="muted">{group.intro}</p>}
      <form className="setting-fields" onSubmit={(e) => void save(e)}>
        {group.fields.map((f) => (
          <FieldControl
            // Remount after a save so secret fields go back to their "Saved …" state.
            key={`${f.key}:${f.is_set}:${f.hint ?? ''}`}
            field={f}
            draft={drafts[f.key]}
            onChange={(v) => setDraft(f, v)}
            onReset={() => resetDraft(f.key)}
          />
        ))}
        <div className="setting-actions">
          <button type="submit" className="btn primary" disabled={!dirty || saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {group.checkable && (
            <button
              type="button"
              className="btn"
              onClick={() => void test()}
              disabled={dirty || check === 'running'}
              title={dirty ? 'Save first, then test' : undefined}
            >
              Test connection
            </button>
          )}
          {source && status?.configured && (
            <button
              type="button"
              className="btn ghost"
              onClick={() => void sync.syncNow(source)}
              disabled={status.running || dirty}
            >
              {status.running ? 'Syncing…' : 'Sync now'}
            </button>
          )}
          {justSaved && !dirty && (
            <span className="ok-text" role="status">
              Saved
            </span>
          )}
        </div>
        {error && (
          <p className="bad-text" role="alert">
            {error}
          </p>
        )}
        {check === 'running' && (
          <p className="check" role="status">
            <Spinner /> Checking…
          </p>
        )}
        {check && check !== 'running' && (
          <p className={`check ${check.ok ? 'ok-text' : 'bad-text'}`} role="status">
            {check.ok ? '✓' : '✗'} {check.message}
          </p>
        )}
        {status?.last_run?.status === 'error' && status.last_run.error && (
          <p className="bad-text">{status.last_run.error}</p>
        )}
        {warnings.length > 0 && (
          <ul className="sync-warnings" aria-label="Warnings from the last sync">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
      </form>
    </section>
  )
}

export function SettingsView() {
  const settings = useApi(endpoints.settings())
  if (settings.error && !settings.data) {
    return <ErrorState error={settings.error} onRetry={settings.reload} what="Settings" />
  }
  if (!settings.data) return <Loading />
  return (
    <div className="settings">
      <header className="page-h">
        <h1>Settings</h1>
        <p>
          Saved to <code>{settings.data.env_file}</code> on this computer. Keys and passwords stay in that file; this
          page only shows their last four characters.
        </p>
      </header>
      {settings.data.groups.map((g) => (
        <GroupCard key={g.id} group={g} onSaved={settings.reload} />
      ))}
    </div>
  )
}
