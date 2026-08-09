import { useCallback, useEffect, useMemo, useState } from 'react'

import { ConnectionsView } from './components/ConnectionsView'
import { DefaultsBrowser } from './components/DefaultsBrowser'
import { Editor } from './components/Editor'
import { FileTree } from './components/FileTree'
import { DefinitionsPanel } from './components/DefinitionsPanel'
import { MaintenanceView } from './components/MaintenanceView'
import {
  NewDefinitionDialog,
  type DefinitionKind,
  type EditTarget,
} from './components/NewDefinitionDialog'
import { SettingsView } from './components/SettingsView'
import { ValidationPanel } from './components/ValidationPanel'
import { WorkspaceOpener } from './components/WorkspaceOpener'
import {
  ApiError,
  api,
  type Catalog,
  type ConfigCandidate,
  type ConnectionState,
  type FileNode,
  type FileReference,
  type Status,
  type ValidationResult,
} from './lib/api'

type View = 'files' | 'defaults' | 'settings' | 'connections' | 'maintenance'

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
  const [references, setReferences] = useState<FileReference[]>([])
  const [file, setFile] = useState<OpenFile | null>(null)
  const [workspaceResults, setWorkspaceResults] = useState<ValidationResult[] | null>(null)
  const [revealLine, setRevealLine] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; tone: 'ok' | 'bad' } | null>(null)
  const [view, setView] = useState<View>('files')
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [creating, setCreating] = useState<DefinitionKind | null>(null)
  const [editing, setEditing] = useState<{ kind: DefinitionKind; target: EditTarget } | null>(null)
  // Held on the backend so it survives a reload; mirrored here for rendering. Its
  // libraries let the builder filter to each file's real library type.
  const [connection, setConnection] = useState<ConnectionState | null>(null)

  const notify = useCallback((text: string, tone: 'ok' | 'bad' = 'ok') => {
    setToast({ text, tone })
    setTimeout(() => setToast(null), 4000)
  }, [])

  const refreshStatus = useCallback(async () => {
    setStatus(await api.status())
  }, [])

  useEffect(() => {
    refreshStatus().catch(() => undefined)
    api.catalog().then(setCatalog).catch(() => undefined)
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
  const loadReferences = useCallback(async () => {
    if (!activeConfig) return
    try {
      const { references: refs } = await api.references(activeConfig)
      setReferences(refs)
      setReferenced(new Set(refs.filter((r) => r.relative).map((r) => r.relative!)))
    } catch {
      setReferences([])
      setReferenced(new Set())
    }
  }, [activeConfig])

  useEffect(() => {
    loadReferences()
  }, [loadReferences])

  // Restore the connection the backend is holding. Passing the config lets it adopt a
  // token already saved there, so an existing Kometa user never has to sign in.
  useEffect(() => {
    api
      .connections(activeConfig)
      .then(setConnection)
      .catch(() => undefined)
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

  const libraries = useMemo(
    () => configs.find((c) => c.path === activeConfig)?.libraries ?? [],
    [configs, activeConfig],
  )

  // Collection files this config already reads — a new collection has to land somewhere
  // Kometa will actually load.
  const collectionTargets = useMemo(() => {
    const referencedCollections = references
      .filter((r) => r.listKey === 'collection_files' && r.relative)
      .map((r) => r.relative!)
    return [...new Set(referencedCollections)]
  }, [references])

  const overlayTargets = useMemo(() => {
    const files = references
      .filter((r) => r.listKey === 'overlay_files' && r.relative)
      .map((r) => r.relative!)
    return [...new Set(files)]
  }, [references])

  // Which library each definition file belongs to, so a preview knows what to search.
  const libraryByFile = useMemo(() => {
    const out: Record<string, string> = {}
    for (const ref of references) {
      if (!ref.relative || !ref.library) continue
      out[ref.relative] ??= ref.library
    }
    return out
  }, [references])

  // Which library type each collection file serves, so the builder can hide builders that
  // cannot apply to it. Needs Plex: the config alone rarely states `library_type`.
  const libraryTypeByFile = useMemo(() => {
    const typeByLibrary = new Map((connection?.libraries ?? []).map((l) => [l.name, l.libraryType]))
    const out: Record<string, string> = {}
    for (const ref of references) {
      if (ref.listKey !== 'collection_files' || !ref.relative || !ref.library) continue
      const type = typeByLibrary.get(ref.library)
      // A file shared between a movie and a show library must not be narrowed to either.
      if (type) out[ref.relative] = ref.relative in out && out[ref.relative] !== type ? 'any' : type
    }
    return out
  }, [references, connection])

  if (!status) return <Centered>Connecting to the backend…</Centered>
  if (!status.workspace) return <WorkspaceOpener onOpened={loadWorkspace} />

  return (
    <div className="flex h-full flex-col">
      <Header
        status={status}
        configs={configs}
        activeConfig={activeConfig}
        dirty={dirty}
        view={view}
        canCreateCollection={Boolean(catalog) && collectionTargets.length > 0}
        canCreateOverlay={Boolean(catalog) && overlayTargets.length > 0}
        onSelectView={setView}
        onSelectConfig={setActiveConfig}
        onToggleWrites={toggleWrites}
        onSave={save}
        onNew={setCreating}
      />

      {view === 'maintenance' ? (
        <MaintenanceView
          config={activeConfig}
          activeFile={file?.path ?? null}
          canWrite={status.workspace.allowWrites}
          onChanged={() => {
            loadReferences()
            if (file) openFile(file.path)
          }}
          notify={notify}
        />
      ) : view === 'settings' ? (
        <SettingsView
          config={activeConfig}
          libraries={libraries}
          canWrite={status.workspace.allowWrites}
          onSaved={() => {
            loadReferences()
            if (file?.path === activeConfig) openFile(activeConfig)
          }}
          notify={notify}
        />
      ) : view === 'connections' ? (
        <ConnectionsView
          config={activeConfig}
          canWrite={status.workspace.allowWrites}
          connection={connection}
          onConnectionChange={setConnection}
          onConfigChanged={() => {
            loadWorkspace()
            loadReferences()
            if (file?.path === activeConfig) openFile(activeConfig)
          }}
          notify={notify}
        />
      ) : view === 'defaults' ? (
        catalog && activeConfig ? (
          <DefaultsBrowser
            catalog={catalog}
            config={activeConfig}
            libraries={libraries}
            onChanged={() => {
              loadReferences()
              if (file?.path === activeConfig) openFile(activeConfig)
            }}
            notify={notify}
          />
        ) : (
          <Centered>Loading the Kometa catalog…</Centered>
        )
      ) : (
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

        {file && (
          <aside className="flex w-72 shrink-0 flex-col border-r border-ink-800 bg-ink-900">
            <DefinitionsPanel
              key={file.path}
              path={file.path}
              canWrite={status.workspace.allowWrites}
              onEdit={(kind, name, definition) =>
                setEditing({
                  kind: kind === 'overlay' ? 'overlay' : 'collection',
                  target: { path: file.path, name, definition },
                })
              }
              onChanged={() => openFile(file.path)}
              notify={notify}
            />
          </aside>
        )}

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
      )}

      {(creating || editing) && catalog && (
        <NewDefinitionDialog
          catalog={catalog}
          kind={editing?.kind ?? creating!}
          editing={editing?.target ?? null}
          targets={
            editing
              ? [editing.target.path]
              : creating === 'overlay'
                ? overlayTargets
                : collectionTargets
          }
          libraryTypeByFile={libraryTypeByFile}
          libraryByFile={libraryByFile}
          onClose={() => {
            setCreating(null)
            setEditing(null)
          }}
          onCreated={(path) => {
            setView('files')
            openFile(path)
            loadReferences()
          }}
          notify={notify}
        />
      )}

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
  view,
  canCreateCollection,
  canCreateOverlay,
  onSelectView,
  onSelectConfig,
  onToggleWrites,
  onSave,
  onNew,
}: {
  status: Status
  configs: ConfigCandidate[]
  activeConfig: string | null
  dirty: boolean
  view: View
  canCreateCollection: boolean
  canCreateOverlay: boolean
  onSelectView: (view: View) => void
  onSelectConfig: (path: string) => void
  onToggleWrites: () => void
  onSave: () => void
  onNew: (kind: DefinitionKind) => void
}) {
  const workspace = status.workspace!
  const engine = status.validationEngine

  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-ink-800 bg-ink-900 px-3 py-2">
      <span className="font-semibold text-ink-100">KometaUI</span>

      <nav className="flex shrink-0 overflow-hidden rounded border border-ink-700">
        {(['files', 'defaults', 'settings', 'connections', 'maintenance'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => onSelectView(tab)}
            className={`whitespace-nowrap px-2.5 py-0.5 capitalize ${
              view === tab ? 'bg-accent-dim/40 text-ink-100' : 'text-ink-400 hover:bg-ink-800'
            }`}
          >
            {tab}
          </button>
        ))}
      </nav>

      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          onClick={() => onNew('collection')}
          disabled={!canCreateCollection || !workspace.allowWrites}
          className={newButtonClass}
          title={
            !workspace.allowWrites
              ? 'Unlock writes to create collections'
              : !canCreateCollection
                ? 'This config references no collection files yet'
                : 'Build a collection from Kometa’s builders'
          }
        >
          + Collection
        </button>
        <button
          type="button"
          onClick={() => onNew('overlay')}
          disabled={!canCreateOverlay || !workspace.allowWrites}
          className={newButtonClass}
          title={
            !canCreateOverlay
              ? 'This config references no overlay files yet'
              : 'Build an overlay from Kometa’s builders'
          }
        >
          + Overlay
        </button>
      </div>

      <span className="min-w-0 flex-1 truncate font-mono text-ink-500" title={workspace.path}>
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

      <div className="ml-auto flex shrink-0 items-center gap-2">
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

const newButtonClass =
  'whitespace-nowrap rounded border border-ink-700 px-2 py-0.5 text-ink-200 hover:bg-ink-800 disabled:opacity-30'

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
