/**
 * Two things that make a live config safe to work on.
 *
 * **Outdated keys.** Configs outlive the Kometa version they were written for, and the
 * failure mode is silence — Kometa stops reading a key and never says so. Each finding
 * comes with the mechanical rewrite, separated into changes that are pure renames and
 * changes that alter what Kometa does.
 *
 * **History.** Every save has always taken a timestamped backup; nothing surfaced them.
 * Here they are, diffable against the current file and restorable.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  api,
  type BackupEntry,
  type EditResult,
  type MigrationFinding,
} from '../lib/api'
import { DiffDialog } from './DiffDialog'

interface Props {
  config: string | null
  /** File whose history to show; falls back to the config. */
  activeFile: string | null
  canWrite: boolean
  onChanged: () => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

export function MaintenanceView({ config, activeFile, canWrite, onChanged, notify }: Props) {
  const [findings, setFindings] = useState<MigrationFinding[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [pending, setPending] = useState<{ result: EditResult; ids: string[] } | null>(null)
  const [busy, setBusy] = useState(false)

  const historyPath = activeFile ?? config
  const [backups, setBackups] = useState<BackupEntry[]>([])
  const [restoring, setRestoring] = useState<{ stamp: string; diff: string[]; stats: { added: number; removed: number } } | null>(null)

  const scan = useCallback(async () => {
    if (!config) return
    setFindings(null)
    try {
      const result = await api.scanMigrations(config)
      setFindings(result.findings)
      // Pre-select the safe ones; anything that changes behaviour is opted into by hand.
      setSelected(new Set(result.findings.filter((f) => !f.changesBehaviour).map((f) => f.id)))
    } catch (e) {
      setFindings([])
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }, [config, notify])

  const loadBackups = useCallback(async () => {
    if (!historyPath) return
    try {
      setBackups((await api.listBackups(historyPath)).backups)
    } catch {
      setBackups([])
    }
  }, [historyPath])

  useEffect(() => {
    scan()
  }, [scan])
  useEffect(() => {
    loadBackups()
  }, [loadBackups])

  async function reviewFixes() {
    if (!config || selected.size === 0) return
    setBusy(true)
    try {
      const ids = [...selected]
      setPending({ result: await api.applyMigrations(config, ids, true), ids })
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  async function applyFixes() {
    if (!config || !pending) return
    setBusy(true)
    try {
      const result = await api.applyMigrations(config, pending.ids)
      notify(`Applied ${result.applied.length} fix${result.applied.length === 1 ? '' : 'es'}`)
      setPending(null)
      await scan()
      onChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  async function reviewRestore(stamp: string) {
    if (!historyPath) return
    try {
      const { diff, stats } = await api.backupDiff(historyPath, stamp)
      // backupDiff reads backup -> current; restoring reverses it, so present it that way.
      setRestoring({ stamp, diff: invertDiff(diff), stats: { added: stats.removed, removed: stats.added } })
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function confirmRestore() {
    if (!historyPath || !restoring) return
    setBusy(true)
    try {
      await api.restoreBackup(historyPath, restoring.stamp)
      notify(`Restored ${historyPath} from ${formatStamp(restoring.stamp)}`)
      setRestoring(null)
      await loadBackups()
      onChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  const behaviourChanges = (findings ?? []).filter((f) => f.changesBehaviour)
  const safeChanges = (findings ?? []).filter((f) => !f.changesBehaviour)

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-5">
        <section>
          <h2 className="text-ink-100">Outdated keys</h2>
          <p className="mb-2 mt-0.5 text-ink-500">
            Keys Kometa has renamed or stopped reading in {config ?? 'your config'}.
          </p>

          {findings === null ? (
            <p className="text-ink-500">Scanning…</p>
          ) : findings.length === 0 ? (
            <p className="text-ok">✓ Nothing outdated found.</p>
          ) : (
            <>
              {safeChanges.length > 0 && (
                <Group title="Safe rewrites" hint="Renames only — Kometa behaves identically.">
                  {safeChanges.map((finding) => (
                    <FindingRow
                      key={finding.id}
                      finding={finding}
                      checked={selected.has(finding.id)}
                      onToggle={() => setSelected(toggle(selected, finding.id))}
                    />
                  ))}
                </Group>
              )}

              {behaviourChanges.length > 0 && (
                <Group
                  title="Needs review"
                  hint="These change what Kometa does. Read the detail before selecting them."
                  tone="warn"
                >
                  {behaviourChanges.map((finding) => (
                    <FindingRow
                      key={finding.id}
                      finding={finding}
                      checked={selected.has(finding.id)}
                      onToggle={() => setSelected(toggle(selected, finding.id))}
                    />
                  ))}
                </Group>
              )}

              <div className="mt-3 flex items-center gap-2">
                <span className="text-ink-500">{selected.size} selected</span>
                <button
                  type="button"
                  onClick={reviewFixes}
                  disabled={!canWrite || selected.size === 0 || busy}
                  className="ml-auto rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
                  title={!canWrite ? 'Unlock writes to apply' : undefined}
                >
                  Review changes
                </button>
              </div>
            </>
          )}
        </section>

        <section>
          <h2 className="text-ink-100">History</h2>
          <p className="mb-2 mt-0.5 text-ink-500">
            Versions saved before each write to{' '}
            <span className="font-mono text-ink-400">{historyPath ?? '—'}</span>.
          </p>

          {backups.length === 0 ? (
            <p className="text-ink-500">No earlier versions yet.</p>
          ) : (
            <ul className="space-y-1">
              {backups.map((backup) => (
                <li
                  key={backup.stamp}
                  className="flex items-center gap-2 rounded border border-ink-800 bg-ink-900 px-2.5 py-1.5"
                >
                  <span className="min-w-0 flex-1 text-ink-200">{formatStamp(backup.stamp)}</span>
                  <span className="text-[11px] text-ink-600">{backup.size} bytes</span>
                  <button
                    type="button"
                    onClick={() => reviewRestore(backup.stamp)}
                    disabled={!canWrite}
                    className="rounded border border-ink-700 px-2 py-0.5 text-[11px] text-ink-200 hover:bg-ink-800 disabled:opacity-30"
                  >
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {pending && (
        <DiffDialog
          title="Apply fixes"
          path={config ?? ''}
          diff={pending.result.diff ?? []}
          stats={pending.result.stats}
          validation={pending.result.validation}
          busy={busy}
          confirmLabel="Apply"
          warning={
            behaviourChanges.some((f) => pending.ids.includes(f.id))
              ? 'This includes a change that alters what Kometa does, not just how it is written.'
              : undefined
          }
          onConfirm={applyFixes}
          onCancel={() => setPending(null)}
        />
      )}

      {restoring && historyPath && (
        <DiffDialog
          title={`Restore from ${formatStamp(restoring.stamp)}`}
          path={historyPath}
          diff={restoring.diff}
          stats={restoring.stats}
          busy={busy}
          confirmLabel="Restore"
          warning="The current contents are backed up first, so this is itself undoable."
          onConfirm={confirmRestore}
          onCancel={() => setRestoring(null)}
        />
      )}
    </div>
  )
}

function toggle(set: Set<string>, id: string): Set<string> {
  const next = new Set(set)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/** Flip a diff's direction so it reads as the change about to be applied. */
function invertDiff(diff: string[]): string[] {
  return diff.map((line) => {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) return line
    if (line.startsWith('+')) return `-${line.slice(1)}`
    if (line.startsWith('-')) return `+${line.slice(1)}`
    return line
  })
}

/** `20260809-053010-347626` -> `2026-08-09 05:30:10`. The sub-second part exists only to
 *  keep filenames unique and is not worth showing. */
function formatStamp(stamp: string): string {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:-\d+)?$/.exec(stamp)
  if (!match) return stamp
  const [, y, mo, d, h, mi, s] = match
  return `${y}-${mo}-${d} ${h}:${mi}:${s}`
}

function Group({
  title,
  hint,
  tone,
  children,
}: {
  title: string
  hint: string
  tone?: 'warn'
  children: React.ReactNode
}) {
  return (
    <div className="mt-3">
      <h3 className={tone === 'warn' ? 'text-warn' : 'text-ink-300'}>{title}</h3>
      <p className="mb-1.5 text-[11px] text-ink-500">{hint}</p>
      <ul className="space-y-1">{children}</ul>
    </div>
  )
}

function FindingRow({
  finding,
  checked,
  onToggle,
}: {
  finding: MigrationFinding
  checked: boolean
  onToggle: () => void
}) {
  return (
    <li className="rounded border border-ink-800 bg-ink-900 px-2.5 py-1.5">
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={!finding.fixable}
          className="mt-0.5 shrink-0"
        />
        <span className="min-w-0 flex-1">
          <span className="text-ink-100">{finding.message}</span>
          <span className="ml-2 font-mono text-[10px] text-ink-600">{finding.location}</span>
          {finding.detail && (
            <span className="mt-0.5 block text-[11px] leading-snug text-ink-500">
              {finding.detail}
            </span>
          )}
        </span>
      </label>
    </li>
  )
}
