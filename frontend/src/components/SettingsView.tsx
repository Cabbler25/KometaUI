/**
 * Edit config.yml's global `settings` and each library's `operations` as forms.
 *
 * Both are large (42 and 33 fields) and almost entirely documented in Kometa's schema, so
 * they render straight from the form model with no bespoke code. Saving reconciles the
 * mapping key by key on the backend, so changing one field produces a one-line diff
 * rather than re-emitting the whole block.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type FormField } from '../lib/api'
import { SchemaForm, type FormValues } from './SchemaForm'

interface Props {
  config: string | null
  libraries: string[]
  canWrite: boolean
  onSaved: () => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

type Target =
  | { kind: 'settings' }
  | { kind: 'operations'; library: string }

export function SettingsView({ config, libraries, canWrite, onSaved, notify }: Props) {
  const [target, setTarget] = useState<Target>({ kind: 'settings' })
  const [fields, setFields] = useState<FormField[] | null>(null)
  const [saved, setSaved] = useState<FormValues>({})
  const [values, setValues] = useState<FormValues>({})
  const [saving, setSaving] = useState(false)

  const pointer =
    target.kind === 'settings' ? ['settings'] : ['libraries', target.library, 'operations']

  const load = useCallback(async () => {
    if (!config) return
    setFields(null)
    const definition = target.kind === 'settings' ? 'settings' : 'operations'
    try {
      const [model, current] = await Promise.all([
        api.formModel('config', definition),
        api.readValue(config, pointer.join('.')),
      ])
      setFields(model.fields)
      const initial = (current.exists && current.value ? current.value : {}) as FormValues
      setSaved(initial)
      setValues(initial)
    } catch (e) {
      setFields([])
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
    // `pointer` is derived from `target`; listing it would re-run on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, target, notify])

  useEffect(() => {
    load()
  }, [load])

  const dirty = JSON.stringify(values) !== JSON.stringify(saved)

  async function save() {
    if (!config) return
    setSaving(true)
    try {
      const result = await api.mergeMapping(config, pointer, values)
      setSaved(values)
      notify(result.changed ? `Saved to ${config}` : 'No changes to save')
      onSaved()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setSaving(false)
    }
  }

  if (!config) return <Centered>Select a config file first.</Centered>

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2">
        <button
          type="button"
          onClick={() => setTarget({ kind: 'settings' })}
          className={tabClass(target.kind === 'settings')}
        >
          Global settings
        </button>
        {libraries.map((library) => (
          <button
            key={library}
            type="button"
            onClick={() => setTarget({ kind: 'operations', library })}
            className={tabClass(target.kind === 'operations' && target.library === library)}
          >
            {library} operations
          </button>
        ))}
        {libraries.length === 0 && (
          <span className="text-ink-500">
            No libraries in this config — add one from the Connections tab.
          </span>
        )}
        <span className="ml-auto font-mono text-[11px] text-ink-600">{pointer.join('.')}</span>
      </div>

      {fields === null ? (
        <Centered>Loading…</Centered>
      ) : fields.length === 0 ? (
        <Centered>No editable fields found.</Centered>
      ) : (
        <SchemaForm
          fields={fields}
          values={values}
          onChange={setValues}
          emptyHint={
            target.kind === 'settings'
              ? 'Nothing overridden — Kometa’s defaults apply. Use “Show all” to change one.'
              : 'No operations configured for this library. Use “Show all” to add one.'
          }
        />
      )}

      <div className="flex shrink-0 items-center gap-3 border-t border-ink-800 px-3 py-2">
        <span className="text-ink-500">
          {Object.keys(values).length} set{dirty ? ' · unsaved changes' : ''}
        </span>
        <button
          type="button"
          onClick={() => setValues(saved)}
          disabled={!dirty}
          className="ml-auto rounded border border-ink-700 px-3 py-1 text-ink-300 hover:bg-ink-800 disabled:opacity-30"
        >
          Revert
        </button>
        <button
          type="button"
          onClick={save}
          disabled={!dirty || !canWrite || saving}
          className="rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
          title={!canWrite ? 'Unlock writes to save' : undefined}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

function tabClass(active: boolean) {
  return `rounded border px-2.5 py-1 ${
    active
      ? 'border-accent-dim bg-accent-dim/30 text-ink-100'
      : 'border-ink-700 text-ink-400 hover:bg-ink-800'
  }`
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-1 items-center justify-center p-6 text-ink-500">{children}</div>
}
