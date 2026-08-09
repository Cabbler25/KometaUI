/**
 * Build a custom collection by picking a builder and filling a form.
 *
 * The 137 builders are grouped by the service that provides them, and filtered to the
 * target library's type — a Movies library never offers `tmdb_show`. Both facts come from
 * the generated catalog, lifted out of Kometa's own Python constants.
 */

import { useEffect, useMemo, useState } from 'react'
import { ApiError, api, type Catalog, type FormField } from '../lib/api'
import { SchemaForm, type FormValues } from './SchemaForm'

interface Props {
  catalog: Catalog
  /** Collection files the user could add to. */
  targets: string[]
  /**
   * Library type serving each collection file, when known. Populated from Plex via the
   * Connections tab; absent entries fall back to showing every builder.
   */
  libraryTypeByFile: Record<string, string>
  onClose: () => void
  onCreated: (path: string) => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

export function NewCollectionDialog({
  catalog,
  targets,
  libraryTypeByFile,
  onClose,
  onCreated,
  notify,
}: Props) {
  const [target, setTarget] = useState(targets[0] ?? '')
  const [name, setName] = useState('')
  const [builder, setBuilder] = useState<string | null>(null)
  const [builderField, setBuilderField] = useState<FormField | null>(null)
  const [builderValue, setBuilderValue] = useState<unknown>(undefined)
  const [details, setDetails] = useState<FormValues>({})
  const [detailFields, setDetailFields] = useState<FormField[]>([])
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)

  // The target file determines which library the collection lands in, and therefore which
  // builders can apply to it.
  const libraryType = libraryTypeByFile[target] ?? 'any'

  // Builders Kometa cannot apply to this library type are noise, so filter them out.
  const excluded = useMemo(() => {
    const groups = catalog.builder_groups
    const out = new Set<string>()
    if (libraryType === 'movie') {
      ;(groups.show_only ?? []).forEach((b) => out.add(b))
      ;(groups.music_only ?? []).forEach((b) => out.add(b))
    } else if (libraryType === 'show') {
      ;(groups.movie_only ?? []).forEach((b) => out.add(b))
      ;(groups.music_only ?? []).forEach((b) => out.add(b))
    } else if (libraryType === 'artist') {
      ;(groups.movie_only ?? []).forEach((b) => out.add(b))
      ;(groups.show_only ?? []).forEach((b) => out.add(b))
    }
    // With an unknown library type, filtering would hide builders that may be perfectly
    // valid, so show everything rather than guess.
    return out
  }, [catalog, libraryType])

  const services = useMemo(() => {
    const q = query.trim().toLowerCase()
    return Object.entries(catalog.services)
      .map(([key, meta]) => ({
        key,
        label: meta.label,
        builders: meta.builders.filter((b) => !excluded.has(b) && (!q || b.includes(q))),
      }))
      .filter((s) => s.builders.length > 0)
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [catalog, excluded, query])

  useEffect(() => {
    if (!builder) return
    let cancelled = false
    api
      .builderForm(builder)
      .then((r) => !cancelled && setBuilderField(r.field))
      .catch(() => !cancelled && setBuilderField(null))
    return () => {
      cancelled = true
    }
  }, [builder])

  // The non-builder attributes (sort_title, sync_mode, summary, artwork…) come from the
  // same schema; keep only those so the picker above stays the single source of builders.
  useEffect(() => {
    let cancelled = false
    api
      .formModel('collection', 'collection-definition')
      .then(({ fields }) => {
        if (cancelled) return
        const builders = new Set(catalog.builder_groups.all ?? [])
        setDetailFields(fields.filter((f) => !builders.has(f.name) && f.name !== 'filters'))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [catalog])

  // Only boolean builders are meaningful with no value (`plex_all: true`). Everything
  // else carries data — a count, an id, a list — and writing `true` there produces a
  // collection Kometa rejects, so those must be filled in before the collection is made.
  const needsValue = Boolean(builderField && builderField.control !== 'boolean')
  const hasValue = builderValue !== undefined && builderValue !== ''

  const definition = useMemo(() => {
    const out: Record<string, unknown> = {}
    if (builder) out[builder] = hasValue ? builderValue : true
    return { ...out, ...details }
  }, [builder, builderValue, hasValue, details])

  const canCreate = Boolean(target && name.trim() && builder && (!needsValue || hasValue))

  async function create() {
    setSaving(true)
    try {
      await api.addCollection(target, name.trim(), definition)
      notify(`Created “${name.trim()}” in ${target}`)
      onCreated(target)
      onClose()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70 p-6">
      <div className="flex h-full max-h-[46rem] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-ink-700 bg-ink-900 shadow-2xl">
        <header className="flex shrink-0 items-center gap-3 border-b border-ink-800 px-4 py-2.5">
          <h2 className="font-semibold text-ink-100">New collection</h2>
          <span className="text-ink-500">
            {services.reduce((n, s) => n + s.builders.length, 0)} builders ·{' '}
            {libraryType === 'any' ? (
              <span title="Connect Plex to filter builders to this library's type">
                library type unknown
              </span>
            ) : (
              `${libraryType} library`
            )}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded px-2 py-0.5 text-ink-400 hover:bg-ink-800"
          >
            ✕
          </button>
        </header>

        <div className="flex shrink-0 items-center gap-2 border-b border-ink-800 px-4 py-2">
          <label htmlFor="c-name" className="text-ink-400">
            Name
          </label>
          <input
            id="c-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Oscar Winners"
            className="w-64 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent"
          />
          <label htmlFor="c-target" className="ml-3 text-ink-400">
            Add to
          </label>
          <select
            id="c-target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-850 px-2 py-1 text-ink-100 outline-none"
          >
            {targets.map((path) => (
              <option key={path} value={path}>
                {path}
              </option>
            ))}
          </select>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-[16rem_minmax(0,1fr)_20rem]">
          <div className="flex min-h-0 flex-col border-r border-ink-800">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search builders…"
              className="m-2 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent"
            />
            <div className="min-h-0 flex-1 overflow-auto pb-2">
              {services.map((service) => (
                <div key={service.key} className="mb-2">
                  <div className="px-3 py-0.5 text-[10px] uppercase tracking-wide text-ink-500">
                    {service.label}
                  </div>
                  {service.builders.map((b) => (
                    <button
                      key={b}
                      type="button"
                      onClick={() => {
                        setBuilder(b)
                        setBuilderValue(undefined)
                      }}
                      className={`block w-full truncate px-3 py-0.5 text-left font-mono text-[11px] ${
                        builder === b
                          ? 'bg-accent-dim/40 text-ink-100'
                          : 'text-ink-300 hover:bg-ink-800'
                      }`}
                    >
                      {b}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>

          <div className="flex min-h-0 flex-col border-r border-ink-800">
            {!builder ? (
              <Centered>Pick a builder to define what goes in this collection.</Centered>
            ) : (
              <>
                <div className="shrink-0 border-b border-ink-800 px-3 py-2">
                  <div className="font-mono text-ink-100">{builder}</div>
                  {builderField?.description && (
                    <p className="mt-0.5 text-[11px] text-ink-500">{builderField.description}</p>
                  )}
                  {catalog.builders_missing_from_schema.includes(builder) && (
                    <p className="mt-1 text-[11px] text-warn">
                      Kometa supports this builder but its JSON schema omits it, so there is no
                      typed form — enter the value directly.
                    </p>
                  )}
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-3">
                  {builderField && (
                    <SchemaForm
                      fields={[builderField]}
                      values={builderValue === undefined ? {} : { [builder]: builderValue }}
                      onChange={(v) => setBuilderValue(v[builder])}
                      filterable={false}
                    />
                  )}
                </div>
                <div className="shrink-0 border-t border-ink-800">
                  <div className="px-3 py-1.5 text-ink-400">Collection settings</div>
                  <div className="max-h-56 overflow-hidden">
                    <SchemaForm fields={detailFields} values={details} onChange={setDetails} />
                  </div>
                </div>
              </>
            )}
          </div>

          <div className="flex min-h-0 flex-col">
            <div className="shrink-0 px-3 py-1.5 text-ink-400">YAML preview</div>
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-3 pb-3 font-mono text-[11px] text-ink-300">
              {name.trim() || builder ? preview(name.trim() || 'Untitled', definition) : '—'}
            </pre>
          </div>
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-ink-800 px-4 py-2.5">
          <span className={needsValue && !hasValue ? 'text-warn' : 'text-ink-500'}>
            {!builder
              ? 'No builder chosen'
              : needsValue && !hasValue
                ? `${builder} needs a value`
                : `1 builder, ${Object.keys(details).length} settings`}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded border border-ink-700 px-3 py-1 text-ink-300 hover:bg-ink-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={create}
            disabled={!canCreate || saving}
            className="rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
          >
            {saving ? 'Creating…' : 'Create collection'}
          </button>
        </footer>
      </div>
    </div>
  )
}

/** A close-enough YAML rendering for the preview pane; the backend writes the real thing. */
function preview(name: string, definition: Record<string, unknown>): string {
  const lines = [`collections:`, `  ${name}:`]
  const entries = Object.entries(definition)
  if (entries.length === 0) lines.push('    # no builder chosen yet')
  for (const [key, value] of entries) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      lines.push(`    ${key}:`)
      for (const [k, v] of Object.entries(value)) lines.push(`      ${k}: ${format(v)}`)
    } else if (Array.isArray(value)) {
      lines.push(`    ${key}:`)
      for (const item of value) lines.push(`      - ${format(item)}`)
    } else {
      lines.push(`    ${key}: ${format(value)}`)
    }
  }
  return lines.join('\n')
}

function format(value: unknown): string {
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-1 items-center justify-center p-6 text-center text-ink-500">{children}</div>
}
