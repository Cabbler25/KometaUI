/**
 * Build a custom collection or overlay by picking a builder and filling a form.
 *
 * The 137 builders are grouped by the service that provides them, and filtered to the
 * target library's type — a Movies library never offers `tmdb_show`. Both facts come from
 * the generated catalog, lifted out of Kometa's own Python constants.
 *
 * Collections and overlays share this dialog because they are the same shape: a set of
 * builders selecting items, plus definition attributes. Only the target key (`collections`
 * vs `overlays`), the schema behind the settings form, and the extra `overlay` field
 * differ, so those are parameters rather than a second component.
 */

import { useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  api,
  type BuilderDocs,
  type Catalog,
  type EditResult,
  type FormField,
  type PreviewResult,
} from '../lib/api'
import { DiffDialog } from './DiffDialog'
import { FiltersEditor, type FilterValues } from './FiltersEditor'
import { SchemaForm, type FormValues } from './SchemaForm'

export type DefinitionKind = 'collection' | 'overlay'

/** An existing definition being edited, rather than a new one being created. */
export interface EditTarget {
  path: string
  name: string
  definition: Record<string, unknown>
}

interface Props {
  catalog: Catalog
  kind: DefinitionKind
  /** Absent when creating. */
  editing?: EditTarget | null
  /** Files of the matching kind the user could add to. */
  targets: string[]
  /**
   * Library type serving each collection file, when known. Populated from Plex via the
   * Connections tab; absent entries fall back to showing every builder.
   */
  libraryTypeByFile: Record<string, string>
  /** Library serving each file, so a preview knows which section to search. */
  libraryByFile: Record<string, string>
  onClose: () => void
  onCreated: (path: string) => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

export function NewDefinitionDialog({
  catalog,
  kind,
  editing = null,
  targets,
  libraryTypeByFile,
  libraryByFile,
  onClose,
  onCreated,
  notify,
}: Props) {
  const isEdit = editing !== null
  const allBuilders = useMemo(
    () => new Set(catalog.builder_groups.all ?? []),
    [catalog],
  )

  // When editing, split the stored definition back into the three things the form edits:
  // its builder, its filters, and everything else.
  const seed = useMemo(() => {
    if (!editing) return null
    const entries = Object.entries(editing.definition)
    const builderEntry = entries.find(([k]) => allBuilders.has(k))
    return {
      builder: builderEntry?.[0] ?? null,
      builderValue: builderEntry?.[1],
      filters: (editing.definition.filters as FilterValues) ?? {},
      details: Object.fromEntries(
        entries.filter(([k]) => !allBuilders.has(k) && k !== 'filters'),
      ) as FormValues,
    }
  }, [editing, allBuilders])

  const [target, setTarget] = useState(editing?.path ?? targets[0] ?? '')
  const [name, setName] = useState(editing?.name ?? '')
  const [builder, setBuilder] = useState<string | null>(seed?.builder ?? null)
  const [builderField, setBuilderField] = useState<FormField | null>(null)
  const [builderValue, setBuilderValue] = useState<unknown>(seed?.builderValue)
  const [builderDocs, setBuilderDocs] = useState<BuilderDocs>({ hint: '', examples: [] })
  const [details, setDetails] = useState<FormValues>(seed?.details ?? {})
  const [filters, setFilters] = useState<FilterValues>(seed?.filters ?? {})
  const [detailFields, setDetailFields] = useState<FormField[]>([])
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [previewNote, setPreviewNote] = useState<string | null>(null)
  const [showDetails, setShowDetails] = useState(Object.keys(seed?.details ?? {}).length > 0)
  const [showFilters, setShowFilters] = useState(Object.keys(seed?.filters ?? {}).length > 0)
  const [pending, setPending] = useState<EditResult | null>(null)

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
    setBuilderDocs({ hint: '', examples: [] })
    api
      .builderForm(builder)
      .then((r) => {
        if (cancelled) return
        setBuilderField(r.field)
        setBuilderDocs({ hint: r.hint, examples: r.examples })
      })
      .catch(() => !cancelled && setBuilderField(null))
    return () => {
      cancelled = true
    }
  }, [builder])

  // The non-builder attributes come from the same schema; keep only those so the picker
  // above stays the single source of builders. For overlays that means `overlay`,
  // positioning and grouping; for collections, sort_title, sync_mode, artwork and so on.
  useEffect(() => {
    let cancelled = false
    const definitionName = kind === 'overlay' ? 'overlay-definition' : 'collection-definition'
    api
      .formModel(kind, definitionName)
      .then(({ fields }) => {
        if (cancelled) return
        const builders = new Set(catalog.builder_groups.all ?? [])
        setDetailFields(fields.filter((f) => !builders.has(f.name) && f.name !== 'filters'))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [catalog, kind])

  // Only boolean builders are meaningful with no value (`plex_all: true`). Everything
  // else carries data — a count, an id, a list — and writing `true` there produces a
  // collection Kometa rejects, so those must be filled in before the collection is made.
  const needsValue = Boolean(builderField && builderField.control !== 'boolean')
  const hasValue = builderValue !== undefined && builderValue !== ''

  const definition = useMemo(() => {
    const out: Record<string, unknown> = {}
    if (builder) out[builder] = hasValue ? builderValue : true
    const composed: Record<string, unknown> = { ...out, ...details }
    if (Object.keys(filters).length > 0) composed.filters = filters
    return composed
  }, [builder, builderValue, hasValue, details, filters])

  const canCreate = Boolean(target && name.trim() && builder && (!needsValue || hasValue))

  const previewLibrary = libraryByFile[target]

  useEffect(() => {
    // A preview describes one specific definition; once that changes the old result is
    // worse than none, so drop it rather than let a stale count linger.
    setPreview(null)
    setPreviewNote(null)
  }, [builder, builderValue, details, target])

  /** Load an example's value into the builder field. */
  async function applyExample(example: string) {
    if (!builder) return
    try {
      const { value } = await api.parseSnippet(example, builder)
      setBuilderValue(value)
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function runPreview() {
    if (!previewLibrary) return
    setPreviewing(true)
    setPreviewNote(null)
    try {
      setPreview(await api.preview(previewLibrary, definition))
    } catch (e) {
      setPreview(null)
      setPreviewNote(e instanceof ApiError ? e.message : String(e))
    } finally {
      setPreviewing(false)
    }
  }

  /** Compute the change without writing, so it can be confirmed first. */
  async function review() {
    setSaving(true)
    try {
      const result = isEdit
        ? await api.saveDefinition(target, kind, editing!.name, definition, true)
        : await api.addDefinition(kind, target, name.trim(), definition, true)
      if (!result.changed) {
        notify('No changes to save')
        return
      }
      setPending(result)
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setSaving(false)
    }
  }

  async function commit() {
    setSaving(true)
    try {
      // A rename is a separate operation from a body edit; do it first so the body save
      // targets the new name.
      let saveName = name.trim()
      if (isEdit && saveName && saveName !== editing!.name) {
        await api.renameDefinition(target, kind, editing!.name, saveName)
      } else if (isEdit) {
        saveName = editing!.name
      }

      if (isEdit) {
        await api.saveDefinition(target, kind, saveName, definition)
        notify(`Saved “${saveName}”`)
      } else {
        await api.addDefinition(kind, target, saveName, definition)
        notify(`Created “${saveName}” in ${target}`)
      }
      setPending(null)
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
          <h2 className="font-semibold text-ink-100">
            {isEdit ? `Edit ${kind}` : `New ${kind === 'overlay' ? 'overlay' : 'collection'}`}
          </h2>
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
            disabled={isEdit}
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-850 px-2 py-1 text-ink-100 outline-none disabled:opacity-50"
            title={isEdit ? 'Moving a definition between files is not supported yet' : undefined}
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
              <Centered>
                Pick a builder to choose which items this {kind} applies to.
              </Centered>
            ) : (
              <>
                <div className="shrink-0 border-b border-ink-800 px-3 py-2">
                  <div className="font-mono text-ink-100">{builder}</div>
                  {(builderDocs.hint || builderField?.description) && (
                    <p className="mt-0.5 text-[11px] text-ink-400">
                      {builderDocs.hint || builderField?.description}
                    </p>
                  )}
                  {catalog.builders_missing_from_schema.includes(builder) && (
                    <p className="mt-1 text-[11px] text-warn">
                      Kometa supports this builder but its JSON schema omits it, so there is no
                      typed form — enter the value directly.
                    </p>
                  )}
                </div>

                <div className="flex min-h-0 flex-1 flex-col overflow-auto">
                  <div className="shrink-0 p-3">
                    {builderField && (
                      <SchemaForm
                        fields={[builderField]}
                        values={builderValue === undefined ? {} : { [builder]: builderValue }}
                        onChange={(v) => setBuilderValue(v[builder])}
                        filterable={false}
                      />
                    )}
                  </div>

                  {/* A generated form can say `plex_search` is an object; only a worked
                      example shows what belongs inside it. These come from the galleries
                      Kometa maintains in json-schema/builders/. */}
                  {builderDocs.examples.length > 0 && (
                    <div className="shrink-0 border-t border-ink-800 px-3 py-2">
                      <p className="mb-1.5 text-ink-400">
                        Examples from Kometa
                        {builderDocs.examples.length > 1 && (
                          <span className="text-ink-600"> ({builderDocs.examples.length})</span>
                        )}
                      </p>
                      <div className="space-y-2">
                        {builderDocs.examples.map((example, i) => (
                          <div key={i} className="rounded border border-ink-800 bg-ink-950/60">
                            <pre className="overflow-x-auto px-2 py-1.5 font-mono text-[11px] leading-snug text-ink-300">
                              {example}
                            </pre>
                            <div className="flex justify-end border-t border-ink-800 px-2 py-1">
                              <button
                                type="button"
                                onClick={() => applyExample(example)}
                                className="text-[11px] text-accent hover:underline"
                              >
                                Use this
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <div className="shrink-0 border-t border-ink-800">
                  <button
                    type="button"
                    onClick={() => setShowFilters((v) => !v)}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink-400 hover:bg-ink-800"
                  >
                    <span className="text-[9px]">{showFilters ? '▼' : '▶'}</span>
                    Filters
                    <span className="text-ink-600">
                      {Object.keys(filters).length > 0
                        ? `${Object.keys(filters).length} applied`
                        : 'narrow the results'}
                    </span>
                  </button>
                  {showFilters && (
                    <div className="max-h-56 overflow-auto border-t border-ink-800 p-3">
                      <FiltersEditor
                        catalog={catalog}
                        libraryType={libraryType}
                        values={filters}
                        onChange={setFilters}
                      />
                    </div>
                  )}
                </div>

                {/* Collapsed by default: choosing a builder is the point of this dialog,
                    and a permanently-open 161-field form crowds out the examples that
                    explain the builder. */}
                <div className="shrink-0 border-t border-ink-800">
                  <button
                    type="button"
                    onClick={() => setShowDetails((v) => !v)}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink-400 hover:bg-ink-800"
                  >
                    <span className="text-[9px]">{showDetails ? '▼' : '▶'}</span>
                    {kind === 'overlay' ? 'Overlay settings' : 'Collection settings'}
                    <span className="text-ink-600">
                      {Object.keys(details).length > 0
                        ? `${Object.keys(details).length} set`
                        : 'optional'}
                    </span>
                  </button>
                  {showDetails && (
                    <div className="flex h-56 min-h-0 flex-col border-t border-ink-800">
                      <SchemaForm fields={detailFields} values={details} onChange={setDetails} />
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          <div className="flex min-h-0 flex-col">
            <div className="shrink-0 px-3 py-1.5 text-ink-400">YAML preview</div>
            <pre className="max-h-48 shrink-0 overflow-auto whitespace-pre-wrap px-3 pb-2 font-mono text-[11px] text-ink-300">
              {name.trim() || builder
                ? renderYaml(name.trim() || 'Untitled', definition, kind)
                : '—'}
            </pre>

            <div className="flex shrink-0 items-center gap-2 border-t border-ink-800 px-3 py-1.5">
              <span className="text-ink-400">Matches</span>
              <button
                type="button"
                onClick={runPreview}
                disabled={!builder || !previewLibrary || previewing}
                className="ml-auto rounded border border-ink-700 px-2 py-0.5 text-[11px] text-ink-200 hover:bg-ink-800 disabled:opacity-30"
                title={
                  !previewLibrary
                    ? 'Connect Plex to preview against your library'
                    : 'Search your library for matching items'
                }
              >
                {previewing ? 'Searching…' : 'Preview'}
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
              {previewNote && <p className="py-2 text-[11px] text-warn">{previewNote}</p>}
              {!preview && !previewNote && (
                <p className="py-2 text-[11px] text-ink-500">
                  {previewLibrary
                    ? 'Run a preview to see which items this would match.'
                    : 'Connect Plex from the Connections tab to preview matches.'}
                </p>
              )}
              {preview && (
                <>
                  <p className="py-1 text-ink-200">
                    {preview.total}
                    {preview.truncated ? '+' : ''} item{preview.total === 1 ? '' : 's'} in{' '}
                    {previewLibrary}
                  </p>
                  {preview.skipped.length > 0 && (
                    // Without this the count silently understates the filtering, which
                    // would be worse than showing nothing.
                    <p className="mb-1 text-[11px] text-warn">
                      Ignored {preview.skipped.map((s) => s.condition).join(', ')} — the count
                      above does not account for {preview.skipped.length === 1 ? 'it' : 'them'}.
                    </p>
                  )}
                  <ul className="space-y-0.5">
                    {preview.items.map((item) => (
                      <li key={item.ratingKey} className="truncate text-[11px] text-ink-300">
                        {item.title}
                        {item.year ? <span className="text-ink-600"> ({item.year})</span> : null}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
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
            onClick={review}
            disabled={!canCreate || saving}
            className="rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
          >
            {saving ? 'Checking…' : isEdit ? 'Review changes' : `Create ${kind}`}
          </button>
        </footer>
      </div>

      {pending && (
        <DiffDialog
          title={isEdit ? 'Review changes' : 'Review new definition'}
          path={target}
          diff={pending.diff ?? []}
          stats={pending.stats}
          validation={pending.validation}
          busy={saving}
          confirmLabel={isEdit ? 'Save' : `Create ${kind}`}
          onConfirm={commit}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  )
}

/** A close-enough YAML rendering for the preview pane; the backend writes the real thing. */
function renderYaml(name: string, definition: Record<string, unknown>, kind: string): string {
  const lines = [`${kind === 'overlay' ? 'overlays' : 'collections'}:`, `  ${name}:`]
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
