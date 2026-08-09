/**
 * Browse and enable Kometa's pre-made collection and overlay defaults.
 *
 * For most users this is the whole product: pick `oscars`, `imdb`, `ribbon`, tune a few
 * options, done — no YAML. The list of defaults, and each one's option schema, come from
 * the generated catalog rather than being hard-coded, so they track Kometa releases.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  api,
  type Catalog,
  type EnabledDefault,
  type FormField,
} from '../lib/api'
import { SchemaForm, type FormValues } from './SchemaForm'

type Kind = 'collection' | 'overlay'

interface Props {
  catalog: Catalog
  config: string
  libraries: string[]
  onChanged: () => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

/** `award/oscars.yml` -> `award`. Kometa groups its defaults by directory. */
function categoryOf(file: string | undefined): string {
  if (!file) return 'other'
  const [dir] = file.split('/')
  return dir.endsWith('.yml') ? 'other' : dir
}

const CATEGORY_LABEL: Record<string, string> = {
  award: 'Awards',
  chart: 'Charts',
  both: 'Movies & Shows',
  movie: 'Movies only',
  show: 'Shows only',
  overlays: 'Overlays',
  other: 'Other',
}

export function DefaultsBrowser({ catalog, config, libraries, onChanged, notify }: Props) {
  const [kind, setKind] = useState<Kind>('collection')
  const [library, setLibrary] = useState(libraries[0] ?? '')
  const [query, setQuery] = useState('')
  const [enabled, setEnabled] = useState<EnabledDefault[]>([])
  const [editing, setEditing] = useState<EnabledDefault | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (!libraries.includes(library) && libraries.length) setLibrary(libraries[0])
  }, [libraries, library])

  const refresh = useCallback(async () => {
    try {
      const { enabled: list } = await api.enabledDefaults(config)
      setEnabled(list)
    } catch {
      setEnabled([])
    }
  }, [config])

  useEffect(() => {
    refresh()
  }, [refresh])

  const group = kind === 'collection' ? catalog.defaults.collections : catalog.defaults.overlays

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase()
    const buckets = new Map<string, string[]>()
    for (const name of group.names) {
      if (q && !name.toLowerCase().includes(q)) continue
      const category = categoryOf(group.files[name])
      if (!buckets.has(category)) buckets.set(category, [])
      buckets.get(category)!.push(name)
    }
    return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [group, query])

  const enabledFor = (name: string) =>
    enabled.find((e) => e.name === name && e.kind === kind && e.library === library)

  async function toggle(name: string) {
    const current = enabledFor(name)
    setBusy(name)
    try {
      if (current) {
        await api.removeDefault(config, library, current.listKey, current.index)
        notify(`Disabled ${name}`)
      } else {
        await api.addDefault(config, library, kind, name)
        notify(`Enabled ${name} for ${library}`)
      }
      await refresh()
      onChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setBusy(null)
    }
  }

  if (!libraries.length) {
    return (
      <Centered>
        No libraries found in <code className="text-ink-300">{config}</code>. Add one before
        enabling defaults.
      </Centered>
    )
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2">
          <Segmented
            value={kind}
            options={[
              { value: 'collection', label: `Collections (${catalog.defaults.collections.names.length})` },
              { value: 'overlay', label: `Overlays (${catalog.defaults.overlays.names.length})` },
            ]}
            onChange={(v) => setKind(v as Kind)}
          />
          <label className="ml-2 text-ink-400" htmlFor="defaults-library">
            Library
          </label>
          <select
            id="defaults-library"
            value={library}
            onChange={(e) => setLibrary(e.target.value)}
            className="rounded border border-ink-700 bg-ink-850 px-2 py-1 text-ink-100 outline-none"
          >
            {libraries.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter…"
            className="ml-auto w-48 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent"
          />
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-3">
          {grouped.map(([category, names]) => (
            <section key={category} className="mb-5">
              <h3 className="mb-2 text-ink-400">{CATEGORY_LABEL[category] ?? category}</h3>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-2">
                {names.map((name) => {
                  const active = enabledFor(name)
                  return (
                    <DefaultCard
                      key={name}
                      name={name}
                      active={Boolean(active)}
                      legacy={active?.legacyKey ?? false}
                      optionCount={Object.keys(active?.templateVariables ?? {}).length}
                      busy={busy === name}
                      onToggle={() => toggle(name)}
                      onConfigure={active ? () => setEditing(active) : undefined}
                    />
                  )
                })}
              </div>
            </section>
          ))}
          {grouped.length === 0 && <Centered>No defaults match “{query}”.</Centered>}
        </div>
      </div>

      {editing && (
        <OptionsPanel
          entry={editing}
          config={config}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await refresh()
            onChanged()
          }}
          notify={notify}
        />
      )}
    </div>
  )
}

function DefaultCard({
  name,
  active,
  legacy,
  optionCount,
  busy,
  onToggle,
  onConfigure,
}: {
  name: string
  active: boolean
  legacy: boolean
  optionCount: number
  busy: boolean
  onToggle: () => void
  onConfigure?: () => void
}) {
  return (
    <div
      className={`rounded border px-2.5 py-2 ${
        active ? 'border-accent-dim bg-accent-dim/10' : 'border-ink-800 bg-ink-900'
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={`min-w-0 flex-1 truncate ${active ? 'text-ink-100' : 'text-ink-300'}`}>
          {name}
        </span>
        <button
          type="button"
          onClick={onToggle}
          disabled={busy}
          className={`shrink-0 rounded px-2 py-0.5 text-[11px] disabled:opacity-40 ${
            active
              ? 'border border-ink-600 text-ink-300 hover:bg-ink-800'
              : 'bg-accent text-ink-950 hover:brightness-110'
          }`}
        >
          {busy ? '…' : active ? 'Remove' : 'Add'}
        </button>
      </div>
      {active && (
        <div className="mt-1.5 flex items-center gap-2 text-[11px]">
          <button
            type="button"
            onClick={onConfigure}
            className="text-accent hover:underline"
          >
            {optionCount ? `${optionCount} option${optionCount === 1 ? '' : 's'} set` : 'Configure'}
          </button>
          {/* Surfacing the legacy spelling explains why an entry looks different in the file. */}
          {legacy && (
            <span className="rounded border border-warn/40 px-1 text-warn" title="Written with the pre-rename `pmm:` key">
              pmm
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function OptionsPanel({
  entry,
  config,
  onClose,
  onSaved,
  notify,
}: {
  entry: EnabledDefault
  config: string
  onClose: () => void
  onSaved: () => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}) {
  const [fields, setFields] = useState<FormField[] | null>(null)
  const [values, setValues] = useState<FormValues>(entry.templateVariables)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    setFields(null)
    setValues(entry.templateVariables)
    api
      .defaultForm(entry.kind, entry.name)
      .then((r) => !cancelled && setFields(r.fields))
      .catch(() => !cancelled && setFields([]))
    return () => {
      cancelled = true
    }
  }, [entry])

  async function save() {
    setSaving(true)
    try {
      await api.setTemplateVariables(config, entry.library!, entry.listKey, entry.index, values)
      notify(`Saved options for ${entry.name}`)
      onSaved()
      onClose()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setSaving(false)
    }
  }

  return (
    <aside className="flex w-[30rem] shrink-0 flex-col border-l border-ink-800 bg-ink-900">
      <header className="flex shrink-0 items-center gap-2 border-b border-ink-800 px-3 py-2">
        <span className="font-medium text-ink-100">{entry.name}</span>
        <span className="text-ink-500">options</span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded px-2 py-0.5 text-ink-400 hover:bg-ink-800"
        >
          ✕
        </button>
      </header>

      {fields === null ? (
        <Centered>Loading options…</Centered>
      ) : fields.length === 0 ? (
        <Centered>This default exposes no documented options.</Centered>
      ) : (
        <SchemaForm
          fields={fields}
          values={values}
          onChange={setValues}
          emptyHint="No options set. Search or “Show all” to customise this default."
        />
      )}

      <footer className="flex shrink-0 items-center gap-2 border-t border-ink-800 px-3 py-2">
        <span className="text-ink-500">{Object.keys(values).length} set</span>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="ml-auto rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-40"
        >
          {saving ? 'Saving…' : 'Save options'}
        </button>
      </footer>
    </aside>
  )
}

function Segmented({
  value,
  options,
  onChange,
}: {
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
}) {
  return (
    <div className="flex overflow-hidden rounded border border-ink-700">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`px-2.5 py-1 ${
            value === option.value ? 'bg-accent-dim/40 text-ink-100' : 'text-ink-400 hover:bg-ink-800'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-1 items-center justify-center p-6 text-center text-ink-500">{children}</div>
}
