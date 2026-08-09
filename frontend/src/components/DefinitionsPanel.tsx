/**
 * The collections or overlays in the open file, as a list you can act on.
 *
 * Creating a definition was already possible; changing one meant editing YAML by hand.
 * This is the entry point to the same generated form, pre-filled — which is what turns
 * the app from a scaffolding tool into one you keep using.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type DefinitionSummary, type EditResult } from '../lib/api'
import { DiffDialog } from './DiffDialog'

interface Props {
  path: string
  onEdit: (kind: string, name: string, definition: Record<string, unknown>) => void
  onChanged: () => void
  canWrite: boolean
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

export function DefinitionsPanel({ path, onEdit, onChanged, canWrite, notify }: Props) {
  const [kind, setKind] = useState<string | null>(null)
  const [definitions, setDefinitions] = useState<DefinitionSummary[] | null>(null)
  const [query, setQuery] = useState('')
  const [pendingDelete, setPendingDelete] = useState<{ name: string; result: EditResult } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setDefinitions(null)
    try {
      const result = await api.listDefinitions(path)
      setKind(result.kind)
      setDefinitions(result.definitions)
    } catch {
      setKind(null)
      setDefinitions([])
    }
  }, [path])

  useEffect(() => {
    load()
  }, [load])

  async function edit(name: string) {
    if (!kind) return
    try {
      const { definition } = await api.readDefinition(path, kind, name)
      onEdit(kind, name, definition)
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function reviewDelete(name: string) {
    if (!kind) return
    try {
      setPendingDelete({ name, result: await api.deleteDefinition(path, kind, name, true) })
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function confirmDelete() {
    if (!pendingDelete || !kind) return
    setBusy(true)
    try {
      await api.deleteDefinition(path, kind, pendingDelete.name)
      notify(`Deleted “${pendingDelete.name}”`)
      setPendingDelete(null)
      await load()
      onChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  if (definitions === null) {
    return <p className="px-3 py-2 text-ink-500">Loading…</p>
  }
  if (!kind || definitions.length === 0) {
    return (
      <p className="px-3 py-2 text-[11px] text-ink-500">
        No collections or overlays in this file.
      </p>
    )
  }

  const q = query.trim().toLowerCase()
  const shown = q ? definitions.filter((d) => d.name.toLowerCase().includes(q)) : definitions

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-ink-800 px-3 py-1.5">
        <span className="text-ink-300">
          {definitions.length} {kind}
          {definitions.length === 1 ? '' : 's'}
        </span>
        {definitions.length > 6 && (
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter…"
            className="ml-auto w-40 rounded border border-ink-700 bg-ink-900 px-2 py-0.5 text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent"
          />
        )}
      </div>

      <ul className="min-h-0 flex-1 overflow-auto">
        {shown.map((definition) => (
          <li
            key={definition.name}
            className="group flex items-center gap-2 border-b border-ink-850 px-3 py-1.5 hover:bg-ink-850"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-ink-100">{definition.name}</div>
              <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-ink-500">
                {definition.builders.length > 0 ? (
                  definition.builders.map((b) => (
                    <span key={b} className="font-mono text-ink-400">
                      {b}
                    </span>
                  ))
                ) : definition.usesTemplate ? (
                  <span>via template</span>
                ) : (
                  <span className="text-warn">no builder</span>
                )}
                {definition.hasFilters && <span>· filtered</span>}
                {definition.settingCount > 0 && <span>· {definition.settingCount} settings</span>}
              </div>
            </div>

            <button
              type="button"
              onClick={() => edit(definition.name)}
              disabled={definition.usesTemplate && definition.builders.length === 0}
              className="shrink-0 rounded border border-ink-700 px-2 py-0.5 text-[11px] text-ink-200 opacity-0 transition-opacity hover:bg-ink-800 group-hover:opacity-100 disabled:opacity-0"
              title={
                definition.usesTemplate && definition.builders.length === 0
                  ? 'Template-driven definitions are edited as YAML'
                  : 'Edit in a form'
              }
            >
              Edit
            </button>
            <button
              type="button"
              onClick={() => reviewDelete(definition.name)}
              disabled={!canWrite}
              className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-ink-500 opacity-0 transition-opacity hover:bg-ink-800 hover:text-danger group-hover:opacity-100 disabled:opacity-0"
              title="Delete"
            >
              ✕
            </button>
          </li>
        ))}
      </ul>

      {pendingDelete && (
        <DiffDialog
          title={`Delete “${pendingDelete.name}”`}
          path={path}
          diff={pendingDelete.result.diff ?? []}
          stats={pendingDelete.result.stats}
          validation={pendingDelete.result.validation}
          busy={busy}
          confirmLabel="Delete"
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}
