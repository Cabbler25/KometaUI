import { useState } from 'react'
import { ApiError, api, type ConfigCandidate } from '../lib/api'

interface Props {
  onOpened: () => void
}

export function WorkspaceOpener({ onOpened }: Props) {
  const [path, setPath] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [configs, setConfigs] = useState<ConfigCandidate[] | null>(null)

  async function open() {
    setBusy(true)
    setError(null)
    try {
      // Opened read-only. Writes are unlocked deliberately from the header, so pointing
      // this at a live config directory cannot modify anything by accident.
      const result = await api.openWorkspace(path, false)
      setConfigs(result.configs)
      onOpened()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-xl">
        <h1 className="text-2xl font-semibold text-ink-100">KometaUI</h1>
        <p className="mt-1 text-ink-400">
          Edit and validate your Kometa configuration. This tool never runs Kometa and never
          writes to Plex.
        </p>

        <label className="mt-8 block text-ink-300" htmlFor="workspace-path">
          Kometa config directory
        </label>
        <div className="mt-1.5 flex gap-2">
          <input
            id="workspace-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && path && open()}
            placeholder="C:\Users\you\Plex Meta Manager\Plex-Meta-Manager\config"
            spellCheck={false}
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 font-mono text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent"
          />
          <button
            type="button"
            onClick={open}
            disabled={!path || busy}
            className="rounded bg-accent px-4 py-1.5 font-medium text-ink-950 hover:brightness-110 disabled:opacity-40"
          >
            {busy ? 'Opening…' : 'Open'}
          </button>
        </div>
        <p className="mt-1.5 text-ink-500">
          The folder containing <code className="text-ink-400">config.yml</code>. Opens read-only.
        </p>

        {error && (
          <div className="mt-4 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
            {error}
          </div>
        )}

        {configs && configs.length > 0 && (
          <div className="mt-6">
            <p className="text-ink-300">
              Found {configs.length} config file{configs.length === 1 ? '' : 's'}:
            </p>
            <ul className="mt-2 space-y-1">
              {configs.map((config) => (
                <li key={config.path} className="flex items-baseline gap-2 text-ink-400">
                  <span className="font-mono text-ink-200">{config.path}</span>
                  {config.isConventionalDefault && (
                    <span className="rounded border border-accent-dim px-1 text-[10px] uppercase text-accent">
                      default
                    </span>
                  )}
                  <span className="truncate">{config.libraries.join(', ')}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
