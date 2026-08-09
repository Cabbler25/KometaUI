/**
 * Row-based editor for a definition's `filters` block.
 *
 * The schema types `filters` as a free-form object, so the generated form can only offer
 * a text box — which is why it was excluded from the builder form until now. Kometa does
 * know the vocabulary though: each attribute belongs to a category (string, tag, date,
 * number, boolean) and each category permits a specific set of modifier suffixes. The
 * catalog carries that map, so every row here is two dropdowns and a value.
 *
 * Filters run *after* the builder, narrowing what it returned.
 */

import { useMemo, useState } from 'react'
import type { Catalog } from '../lib/api'

export type FilterValues = Record<string, unknown>

interface Props {
  catalog: Catalog
  /** Restricts the attribute list; `any` offers everything. */
  libraryType: string
  values: FilterValues
  onChange: (values: FilterValues) => void
}

/** Human labels for Kometa's modifier suffixes. */
const MODIFIER_LABEL: Record<string, string> = {
  '': 'is',
  '.not': 'is not',
  '.is': 'is exactly',
  '.isnot': 'is not exactly',
  '.begins': 'begins with',
  '.ends': 'ends with',
  '.regex': 'matches regex',
  '.gt': 'greater than',
  '.gte': 'at least',
  '.lt': 'less than',
  '.lte': 'at most',
  '.before': 'before',
  '.after': 'after',
  '.count_gt': 'count greater than',
  '.count_gte': 'count at least',
  '.count_lt': 'count less than',
  '.count_lte': 'count at most',
}

const CATEGORY_HINT: Record<string, string> = {
  date: 'A date (2020-01-01), or a relative age like 30 (days) or 6m.',
  number: 'A number.',
  tag: 'One value, or several separated by commas.',
  boolean: 'true or false.',
  string: 'Text.',
  special: 'See the Kometa wiki for this filter’s accepted values.',
}

interface Row {
  key: string
  attribute: string
  modifier: string
  value: unknown
}

/** `audience_rating.gte` -> `['audience_rating', '.gte']`. */
function splitKey(key: string, known: Record<string, { modifiers: string[] }>): [string, string] {
  if (key in known) return [key, '']
  const dot = key.lastIndexOf('.')
  if (dot > 0) {
    const attribute = key.slice(0, dot)
    const modifier = key.slice(dot)
    if (attribute in known && known[attribute].modifiers.includes(modifier)) {
      return [attribute, modifier]
    }
  }
  return [key, '']
}

export function FiltersEditor({ catalog, libraryType, values, onChange }: Props) {
  const [adding, setAdding] = useState(false)
  const attributes = catalog.filters?.attributes ?? {}

  // Kometa scopes filters by library type; a Movies library has no `episode_title`.
  const available = useMemo(() => {
    const scoped = catalog.filters?.by_library_type?.[libraryType]
    const names = scoped && scoped.length ? scoped : Object.keys(attributes)
    return names.filter((n) => n in attributes).sort()
  }, [catalog, libraryType, attributes])

  const rows: Row[] = useMemo(
    () =>
      Object.entries(values).map(([key, value]) => {
        const [attribute, modifier] = splitKey(key, attributes)
        return { key, attribute, modifier, value }
      }),
    [values, attributes],
  )

  function write(next: Row[]) {
    const out: FilterValues = {}
    for (const row of next) {
      if (!row.attribute) continue
      out[`${row.attribute}${row.modifier}`] = row.value
    }
    onChange(out)
  }

  function update(index: number, patch: Partial<Row>) {
    const next = rows.map((row, i) => (i === index ? { ...row, ...patch } : row))
    // Switching attribute can invalidate the modifier, so fall back to the plain form.
    const row = next[index]
    const allowed = attributes[row.attribute]?.modifiers ?? ['']
    if (!allowed.includes(row.modifier)) row.modifier = allowed[0] ?? ''
    write(next)
  }

  function remove(index: number) {
    write(rows.filter((_, i) => i !== index))
  }

  function add(attribute: string) {
    setAdding(false)
    if (!attribute || rows.some((r) => r.attribute === attribute && r.modifier === '')) return
    const category = attributes[attribute]?.category
    write([...rows, { key: attribute, attribute, modifier: '', value: category === 'boolean' ? true : '' }])
  }

  return (
    <div className="space-y-1.5">
      {rows.length === 0 && !adding && (
        <p className="text-[11px] text-ink-500">
          No filters. Filters narrow what the builder returned — useful when a builder has
          no options of its own.
        </p>
      )}

      {rows.map((row, index) => {
        const meta = attributes[row.attribute]
        const modifiers = meta?.modifiers ?? ['']
        const category = meta?.category ?? 'string'
        return (
          <div key={`${row.key}-${index}`} className="flex items-start gap-1.5">
            <select
              value={row.attribute}
              onChange={(e) => update(index, { attribute: e.target.value })}
              className={`${controlClass} w-44 shrink-0`}
            >
              {!(row.attribute in attributes) && (
                // Preserve anything already in the file that we do not recognise, rather
                // than silently dropping it on the next save.
                <option value={row.attribute}>{row.attribute} (unknown)</option>
              )}
              {available.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>

            <select
              value={row.modifier}
              onChange={(e) => update(index, { modifier: e.target.value })}
              disabled={modifiers.length <= 1}
              className={`${controlClass} w-36 shrink-0 disabled:opacity-40`}
            >
              {modifiers.map((modifier) => (
                <option key={modifier} value={modifier}>
                  {MODIFIER_LABEL[modifier] ?? modifier}
                </option>
              ))}
            </select>

            {category === 'boolean' ? (
              <select
                value={String(row.value)}
                onChange={(e) => update(index, { value: e.target.value === 'true' })}
                className={`${controlClass} min-w-0 flex-1`}
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            ) : (
              <input
                value={Array.isArray(row.value) ? row.value.join(', ') : String(row.value ?? '')}
                onChange={(e) => update(index, { value: coerce(e.target.value, category) })}
                placeholder={CATEGORY_HINT[category] ?? ''}
                className={`${controlClass} min-w-0 flex-1`}
              />
            )}

            <button
              type="button"
              onClick={() => remove(index)}
              className="shrink-0 rounded px-1.5 py-1 text-ink-500 hover:bg-ink-800 hover:text-danger"
              title="Remove this filter"
            >
              ✕
            </button>
          </div>
        )
      })}

      {adding ? (
        <select
          autoFocus
          defaultValue=""
          onChange={(e) => add(e.target.value)}
          onBlur={() => setAdding(false)}
          className={`${controlClass} w-44`}
        >
          <option value="" disabled>
            Choose an attribute…
          </option>
          {available.map((name) => (
            <option key={name} value={name}>
              {name} · {attributes[name].category}
            </option>
          ))}
        </select>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="rounded border border-ink-700 px-2 py-0.5 text-[11px] text-ink-300 hover:bg-ink-800"
        >
          + Add filter
        </button>
      )}
    </div>
  )
}

/**
 * Turn typed text into the value that belongs in the YAML.
 *
 * Numeric filters must be written as numbers: quoting them (`audience_rating.gte: '8'`)
 * where the file previously said `7.0` is a needless type change, and it reads as a
 * mistake in the diff even where Kometa would coerce it.
 *
 * A comma means the user is listing alternatives, which Kometa accepts as a sequence —
 * except for dates and numbers, where a comma is not meaningful.
 */
function coerce(raw: string, category: string): unknown {
  const trimmed = raw.trim()
  if (!trimmed) return ''

  if (category === 'number') {
    const asNumber = Number(trimmed)
    return trimmed !== '' && Number.isFinite(asNumber) ? asNumber : raw
  }

  if (category !== 'date' && raw.includes(',')) {
    return raw
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
  }

  return raw
}

const controlClass =
  'rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent'
