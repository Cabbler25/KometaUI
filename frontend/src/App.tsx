import { useCallback, useEffect, useMemo, useState } from 'react'

import { Editor } from './components/Editor'
import { FileTree } from './components/FileTree'
import { ValidationPanel } from './components/ValidationPanel'
import { WorkspaceOpener } from './components/WorkspaceOpener'
import {
  ApiError,
  api,
  type ConfigCandidate,
  type FileNode,
  type Status,
  type ValidationResult,
} from './lib/api'

interface OpenFile {
  path: string
  saved: string
  draft: string
  validation: ValidationResult
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [tree, setTree] = useState<FileNode | null>(null)
  const [configs, setConfigs] = useState<ConfigCandidate[]>([])
  const [activeConfig, setActiveConfig] = useState<string | null>(null)
  const [referenced, setReferenced] = useState<Set<string>>(new Set())
  const [file, setFile] = useState<OpenFile | null>(null)
  const [workspaceResults, setWorkspaceResults] = useState<ValidationResult[] | null>(null)
  const [revealLine, setRevealLine] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; tone: 'ok' | 'bad' } | null>(null)

  const notify = useCallback((text: string, tone: 'ok' | 'bad' = 'ok') => {
    setToast({ text, tone })
    setTimeout(() => setToast(null), 4000)
  }, [])

  const refreshStatus = useCallback(async () => {
    setStatus(await api.status())
  }, [])

  useEffect(() => {
    refreshStatus().catch(() => undefined)
  }, [refreshStatus])

  const loadWorkspace = useCallback(async () => {
    const [nextTree, nextConfigs] = await Promise.all([api.tree(), api.configs()])
    setTree(nextTree)
    setConfigs(nextConfigs.configs)
    const preferred = nextConfigs.configs[0]?.path ?? null
    setActiveConfig(preferred)
    await refreshStatus()
  }, [refreshStatus])

  // Resolve which files the selected config actually pulls in, so the tree can mark the
  // rest as unused — files sitting in the directory that Kometa never reads.
  useEffect(() => {
    if (!activeConfig) return
    let cancelled = false
    api
      .references(activeConfig)
      .then(({ references }) => {
        if (cancelled) return
        const names = references
          .filter((r) => r.exists)
          .map((r) => r.value.replace(/\\/g, '/').split('/').slice(-2).join('/'))
        setReferenced(new Set(names))
      })
      .catch(() => setReferenced(new Set()))
    return () => {
      cancelled = true
    }
  }, [activeConfig])

  const openFile = useCallback(
    async (path: string) => {
      try {
        const result = await api.readFile(path)
        setFile({ path, saved: result.text, draft: result.text, validation: result.validation })
        setWorkspaceResults(null)
      } catch (e) {
        notify(e instanceof ApiError ? e.message : String(e), 'bad')
      }
    },
    [notify],
  )

  const dirty = file != null && file.draft !== file.saved

  const save = useCallback(async () => {
    if (!file || !dirty) return
    try {
      const result = await api.writeFile(file.path, file.draft)
      setFile((prev) =>
        prev && prev.path === file.path
          ? { ...prev, saved: prev.draft, validation: result.validation }
          : prev,
      )
      notify(result.backup ? 'Saved (previous version backed up)' : 'Saved')
    } catch (e) {
      if (e instanceof ApiError && e.isReadOnly) {
        notify('Workspace is read-only — unlock writes in the header to save.', 'bad')
      } else {
        notify(e instanceof ApiError ? e.message : String(e), 'bad')
      }
    }
  }, [file, dirty, notify])

  // Re-validate the draft as the user types, without saving.
  useEffect(() => {
    if (!file || file.draft === file.saved) return
    const handle = setTimeout(() => {
      api
        .validate(file.draft, file.path)
        .then((validation) =>
          setFile((prev) => (prev && prev.path === file.path ? { ...prev, validation } : prev)),
        )
        .catch(() => undefined)
    }, 400)
    return () => clearTimeout(handle)
  }, [file])

  const validateAll = useCallback(async () => {
    setBusy(true)
    try {
      const { results, summary } = await api.validateAll()
      setWorkspaceResults(results)
      notify(
        summary.withErrors === 0
          ? `All ${summary.files} files are clean`
          : `${summary.withErrors} of ${summary.files} files have problems`,
        summary.withErrors === 0 ? 'ok' : 'bad',
      )
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setBusy(false)
    }
  }, [notify])

  const toggleWrites = useCallback(async () => {
    if (!status?.workspace) return
    const next = !status.workspace.allowWrites
    await api.setWrites(next)
    await refreshStatus()
    notify(next ? 'Writes unlocked' : 'Writes locked')
  }, [status, refreshStatus, notify])

  const openFinding = useCallback(
    async (path: string, line: number | null) => {
      if (file?.path !== path) await openFile(path)
      setRevealLine(line)
    },
    [file, openFile],
  )

  const errorPaths = useMemo(() => {
    const paths = new Set<string>()
    for (const result of workspaceResults ?? []) if (!result.ok) paths.add(result.file)
    if (file && !file.validation.ok) paths.add(file.path)
    return paths
  }, [workspaceResults, file])

  useEffect(() => {
    if (status?.workspace && !tree) loadWorkspace().catch(() => undefined)
  }, [status, tree, loadWorkspace])

  if (!status) return <Centered>Connecting to the backend…</Centered>
  if (!status.workspace) return <WorkspaceOpener onOpened={loadWorkspace} />

  return (
    <div className="flex h-full flex-col">
      <Header
        status={status}
        configs={configs}
        activeConfig={activeConfig}
        dirty={dirty}
        onSelectConfig={setActiveConfig}
        onToggleWrites={toggleWrites}
        onSave={save}
      />

      <div className="flex min-h-0 flex-1">
        <aside className="w-64 shrink-0 overflow-auto border-r border-ink-800 bg-ink-900">
          {tree ? (
            <FileTree
              node={tree}
              activePath={file?.path ?? null}
              dirtyPaths={dirty && file ? new Set([file.path]) : new Set()}
              errorPaths={errorPaths}
              referencedPaths={referenced}
              onSelect={openFile}
            />
          ) : (
            <div className="p-3 text-ink-500">Loading…</div>
          )}
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            {file ? (
              <Editor
                path={file.path}
                kind={file.validation.kind}
                text={file.saved}
                readOnly={!status.workspace.allowWrites}
                revealLine={revealLine}
                onChange={(text) =>
                  setFile((prev) => (prev ? { ...prev, draft: text } : prev))
                }
                onSave={save}
              />
            ) : (
              <Centered>Select a file to start editing.</Centered>
            )}
          </div>

          <div className="h-56 shrink-0 border-t border-ink-800">
            <ValidationPanel
              result={file?.validation ?? null}
              workspaceResults={workspaceResults}
              busy={busy}
              onValidateAll={validateAll}
              onOpenFinding={openFinding}
              onClearWorkspaceResults={() => setWorkspaceResults(null)}
            />
          </div>
        </main>
      </div>

      {toast && (
        <div
          className={`fixed bottom-4 right-4 rounded border px-3 py-2 shadow-lg ${
            toast.tone === 'ok'
              ? 'border-ok/40 bg-ink-850 text-ok'
              : 'border-danger/40 bg-ink-850 text-danger'
          }`}
        >
          {toast.text}
        </div>
      )}
    </div>
  )
}

function Header({
  status,
  configs,
  activeConfig,
  dirty,
  onSelectConfig,
  onToggleWrites,
  onSave,
}: {
  status: Status
  configs: ConfigCandidate[]
  activeConfig: string | null
  dirty: boolean
  onSelectConfig: (path: string) => void
  onToggleWrites: () => void
  onSave: () => void
}) {
  const workspace = status.workspace!
  const engine = status.validationEngine

  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-ink-800 bg-ink-900 px-3 py-2">
      <span className="font-semibold text-ink-100">KometaUI</span>
      <span className="truncate font-mono text-ink-500" title={workspace.path}>
        {workspace.path}
      </span>

      {configs.length > 1 && (
        // Real installs keep several configs and choose between them with --config, so
        // which one is "active" is the user's call, not something to infer.
        <select
          value={activeConfig ?? ''}
          onChange={(e) => onSelectConfig(e.target.value)}
          className="rounded border border-ink-700 bg-ink-850 px-1.5 py-0.5 text-ink-200 outline-none"
          title="Which config defines the files in use"
        >
          {configs.map((config) => (
            <option key={config.path} value={config.path}>
              {config.path}
            </option>
          ))}
        </select>
      )}

      <div className="ml-auto flex items-center gap-2">
        <Chip
          tone={engine.kometa_available ? 'ok' : 'muted'}
          title={engine.detail ?? `Using Kometa ${engine.kometa_version}'s own validator`}
        >
          {engine.kometa_available ? `Kometa ${engine.kometa_version}` : 'Bundled schemas'}
        </Chip>

        <button
          type="button"
          onClick={onToggleWrites}
          className={`rounded border px-2 py-0.5 ${
            workspace.allowWrites
              ? 'border-warn/50 text-warn hover:bg-warn/10'
              : 'border-ink-700 text-ink-300 hover:bg-ink-800'
          }`}
          title={
            workspace.allowWrites
              ? 'Writes are unlocked. Click to lock.'
              : 'Workspace is read-only. Click to allow saving.'
          }
        >
          {workspace.allowWrites ? '● Writes unlocked' : '🔒 Read-only'}
        </button>

        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || !workspace.allowWrites}
          className="rounded bg-accent px-3 py-0.5 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
          title="Ctrl/Cmd+S"
        >
          {dirty ? 'Save •' : 'Save'}
        </button>
      </div>
    </header>
  )
}

function Chip({
  children,
  tone,
  title,
}: {
  children: React.ReactNode
  tone: 'ok' | 'muted'
  title?: string
}) {
  return (
    <span
      title={title}
      className={`rounded border px-1.5 py-0.5 text-[11px] ${
        tone === 'ok' ? 'border-ok/40 text-ok' : 'border-ink-700 text-ink-400'
      }`}
    >
      {children}
    </span>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center text-ink-500">{children}</div>
}
