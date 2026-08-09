/**
 * Show exactly which lines a change would touch, before it is written.
 *
 * The whole point of the surgical editor is that a form save produces a two-line diff
 * rather than a reformatted file. That claim is only reassuring if you can see it, so
 * every write path can route through here first.
 */

import type { ValidationResult } from '../lib/api'

interface Props {
  title: string
  path: string
  diff: string[]
  stats?: { added: number; removed: number }
  validation?: ValidationResult
  busy?: boolean
  confirmLabel?: string
  /** Marks the change as one that alters Kometa's behaviour, not just its spelling. */
  warning?: string
  onConfirm: () => void
  onCancel: () => void
}

export function DiffDialog({
  title,
  path,
  diff,
  stats,
  validation,
  busy,
  confirmLabel = 'Apply',
  warning,
  onConfirm,
  onCancel,
}: Props) {
  const errors = validation?.findings.filter((f) => f.severity === 'error') ?? []

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink-950/75 p-6">
      <div className="flex max-h-[40rem] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-ink-700 bg-ink-900 shadow-2xl">
        <header className="flex shrink-0 items-center gap-3 border-b border-ink-800 px-4 py-2.5">
          <h2 className="font-semibold text-ink-100">{title}</h2>
          <span className="truncate font-mono text-ink-500">{path}</span>
          {stats && (
            <span className="ml-auto shrink-0 text-[11px]">
              <span className="text-ok">+{stats.added}</span>{' '}
              <span className="text-danger">−{stats.removed}</span>
            </span>
          )}
        </header>

        {warning && (
          <p className="shrink-0 border-b border-warn/30 bg-warn/10 px-4 py-2 text-warn">
            {warning}
          </p>
        )}

        {errors.length > 0 && (
          // Applying anyway is allowed — a config can be legitimately mid-edit — but the
          // consequence should not be a surprise discovered on the next Kometa run.
          <div className="shrink-0 border-b border-danger/30 bg-danger/10 px-4 py-2">
            <p className="text-danger">
              This would leave {path} with {errors.length} validation error
              {errors.length === 1 ? '' : 's'}:
            </p>
            <ul className="mt-1 space-y-0.5">
              {errors.slice(0, 3).map((finding, i) => (
                <li key={i} className="text-[11px] text-danger/90">
                  {finding.message}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-auto bg-ink-950/40">
          {diff.length === 0 ? (
            <p className="p-6 text-center text-ink-500">Nothing would change.</p>
          ) : (
            <pre className="px-4 py-3 font-mono text-[11px] leading-relaxed">
              {diff.map((line, i) => (
                <div key={i} className={lineClass(line)}>
                  {line || ' '}
                </div>
              ))}
            </pre>
          )}
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-ink-800 px-4 py-2.5">
          <span className="text-ink-500">A backup is taken before writing.</span>
          <button
            type="button"
            onClick={onCancel}
            className="ml-auto rounded border border-ink-700 px-3 py-1 text-ink-300 hover:bg-ink-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy || diff.length === 0}
            className="rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
          >
            {busy ? 'Applying…' : confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  )
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-ink-600'
  if (line.startsWith('@@')) return 'text-accent'
  if (line.startsWith('+')) return 'bg-ok/10 text-ok'
  if (line.startsWith('-')) return 'bg-danger/10 text-danger'
  return 'text-ink-400'
}
