/**
 * Renders a form from schema-derived field descriptors.
 *
 * There is no per-field code anywhere in the app: the backend turns Kometa's JSON Schema
 * into `FormField[]`, and this switches on `control`. That is what makes ~280 collection
 * attributes and 100+ template variables editable without hand-writing them, and what
 * keeps the forms correct when Kometa ships a new release.
 */

import { useMemo, useState } from 'react'
import type { FormField } from '../lib/api'

export type FormValues = Record<string, unknown>

interface Props {
  fields: FormField[]
  values: FormValues
  onChange: (values: FormValues) => void
  /** Hide fields the user has not set, behind a search box. Essential for large forms. */
  filterable?: boolean
  emptyHint?: string
}

export function SchemaForm({ fields, values, onChange, filterable = true, emptyHint }: Props) {
  const [query, setQuery] = useState('')
  const [showAll, setShowAll] = useState(false)

  const set = (name: string, value: unknown) => {
    const next = { ...values }
    // An empty value means "not set" — drop the key so we never write `key: ''` into a
    // config, which Kometa would read as a deliberate empty string.
    if (value === '' || value === undefined || value === null) delete next[name]
    else next[name] = value
    onChange(next)
  }

  const visible = useMemo(() => {
    // Without the search box there is no way to reveal hidden fields, so a
    // non-filterable form must show everything it was given. This is the mode used for
    // single-field forms such as a chosen builder's value.
    if (!filterable) return fields

    const q = query.trim().toLowerCase()
    if (q) {
      return fields.filter(
        (f) => f.name.toLowerCase().includes(q) || f.description.toLowerCase().includes(q),
      )
    }
    if (showAll) return fields
    // Default view: what is already set, plus anything required.
    return fields.filter((f) => f.name in values || f.required)
  }, [fields, values, query, showAll, filterable])

  const setCount = fields.filter((f) => f.name in values).length

  return (
    // `flex-1` and `min-h-0` together are what let the list below scroll: without them
    // this box grows to fit its content and the overflow is simply clipped by whatever
    // contains it, which looked like "search results I cannot reach".
    <div className="flex min-h-0 flex-1 flex-col">
      {filterable && (
        <div className="flex shrink-0 items-center gap-2 border-b border-ink-800 px-3 py-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${fields.length} options…`}
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent"
          />
          <span className="shrink-0 text-ink-500">{setCount} set</span>
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="shrink-0 rounded border border-ink-700 px-2 py-1 text-ink-300 hover:bg-ink-800"
          >
            {showAll ? 'Show set only' : 'Show all'}
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {visible.length === 0 ? (
          <p className="py-6 text-center text-ink-500">
            {emptyHint ?? 'Nothing set yet — use “Show all” or search to add options.'}
          </p>
        ) : (
          <div className="space-y-3">
            {visible.map((field) => (
              <Field
                key={field.name}
                field={field}
                value={values[field.name]}
                isSet={field.name in values}
                onChange={(v) => set(field.name, v)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Field({
  field,
  value,
  isSet,
  onChange,
}: {
  field: FormField
  value: unknown
  isSet: boolean
  onChange: (value: unknown) => void
}) {
  return (
    <div className="grid grid-cols-[minmax(0,14rem)_minmax(0,1fr)] items-start gap-3">
      <label className="pt-1" htmlFor={`f-${field.name}`}>
        <span className={isSet ? 'text-ink-100' : 'text-ink-300'}>{field.label}</span>
        {field.required && <span className="ml-1 text-danger">*</span>}
        <span className="block font-mono text-[10px] text-ink-600">{field.name}</span>
      </label>

      <div className="min-w-0">
        <Control field={field} value={value} onChange={onChange} />
        {field.description && (
          <p className="mt-1 text-[11px] leading-snug text-ink-500">{field.description}</p>
        )}
      </div>
    </div>
  )
}

const inputClass =
  'w-full rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent'

function Control({
  field,
  value,
  onChange,
}: {
  field: FormField
  value: unknown
  onChange: (value: unknown) => void
}) {
  const id = `f-${field.name}`

  switch (field.control) {
    case 'boolean':
      return (
        <select
          id={id}
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value === 'true')}
          className={inputClass}
        >
          <option value="">— not set —</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      )

    case 'select':
      return (
        <select
          id={id}
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value || undefined)}
          className={inputClass}
        >
          <option value="">— not set —</option>
          {field.options.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option) || '(empty)'}
            </option>
          ))}
        </select>
      )

    case 'integer':
    case 'number':
      return (
        <input
          id={id}
          type="number"
          value={value === undefined ? '' : String(value)}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          step={field.control === 'integer' ? 1 : 'any'}
          onChange={(e) =>
            onChange(e.target.value === '' ? undefined : Number(e.target.value))
          }
          className={inputClass}
        />
      )

    case 'multiselect':
      return <MultiSelect field={field} value={value} onChange={onChange} />

    case 'list':
      return (
        <input
          id={id}
          value={Array.isArray(value) ? value.join(', ') : value === undefined ? '' : String(value)}
          placeholder="comma separated"
          onChange={(e) => {
            const parts = e.target.value
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean)
            onChange(parts.length ? parts : undefined)
          }}
          className={inputClass}
        />
      )

    case 'object':
    case 'yaml':
      // No sensible widget for this shape — hand the user the YAML for this field alone
      // rather than a lossy approximation of it.
      return (
        <textarea
          id={id}
          rows={3}
          spellCheck={false}
          value={value === undefined ? '' : toYamlish(value)}
          placeholder="YAML value"
          onChange={(e) => onChange(e.target.value ? fromYamlish(e.target.value) : undefined)}
          className={`${inputClass} font-mono`}
        />
      )

    default:
      return (
        <input
          id={id}
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value || undefined)}
          className={inputClass}
        />
      )
  }
}

function MultiSelect({
  field,
  value,
  onChange,
}: {
  field: FormField
  value: unknown
  onChange: (value: unknown) => void
}) {
  const selected = new Set(Array.isArray(value) ? value.map(String) : [])
  return (
    <div className="flex flex-wrap gap-1">
      {field.options.map((option) => {
        const key = String(option)
        const on = selected.has(key)
        return (
          <button
            key={key}
            type="button"
            onClick={() => {
              const next = new Set(selected)
              if (on) next.delete(key)
              else next.add(key)
              onChange(next.size ? [...next] : undefined)
            }}
            className={`rounded border px-1.5 py-0.5 ${
              on ? 'border-accent bg-accent-dim/40 text-ink-100' : 'border-ink-700 text-ink-400 hover:bg-ink-800'
            }`}
          >
            {key}
          </button>
        )
      })}
    </div>
  )
}

/** Render a structured value compactly for the raw-YAML escape hatch. */
function toYamlish(value: unknown): string {
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

/** Accept either JSON or a bare scalar from the escape hatch. */
function fromYamlish(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  if (/^[[{]/.test(trimmed)) {
    try {
      return JSON.parse(trimmed)
    } catch {
      return text
    }
  }
  return text
}
