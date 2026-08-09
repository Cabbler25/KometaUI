/**
 * Set up the connections Kometa needs, and confirm they work before it runs.
 *
 * Two things this fixes. Getting a Plex token normally means digging through an item's
 * XML for `X-Plex-Token`; here you approve access on plex.tv instead. And library names in
 * config.yml must match the server character for character — a typo is a silent no-op in
 * Kometa rather than an error — so the real names are read off the server.
 *
 * Connection state lives on the backend, not in this component. That is what lets a page
 * reload pick up where you left off, and it means the token itself never reaches the
 * browser: every Plex call is made server-side with the token it holds.
 *
 * Strictly read-only against Plex. Only your own config files are written.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  api,
  type ConnectionState,
  type PlexLibrary,
  type PlexPin,
  type PlexServer,
} from '../lib/api'

interface Props {
  config: string | null
  canWrite: boolean
  connection: ConnectionState | null
  onConnectionChange: (state: ConnectionState) => void
  onConfigChanged: () => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

export function ConnectionsView({
  config,
  canWrite,
  connection,
  onConnectionChange,
  onConfigChanged,
  notify,
}: Props) {
  const [url, setUrl] = useState('')
  const [apikey, setApikey] = useState('')
  const [manualToken, setManualToken] = useState('')
  const [pin, setPin] = useState<PlexPin | null>(null)
  const [servers, setServers] = useState<PlexServer[]>([])
  const [testing, setTesting] = useState(false)
  const [testingTmdb, setTestingTmdb] = useState(false)
  const [saving, setSaving] = useState(false)
  const pollTimer = useRef<number | null>(null)

  // Adopt whatever the server already knows, including after a reload.
  useEffect(() => {
    if (connection?.url && !url) setUrl(connection.url)
  }, [connection, url])

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current)
      pollTimer.current = null
    }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  async function signIn() {
    stopPolling()
    try {
      const started = await api.plexPin()
      setPin(started)
      window.open(started.authUrl, '_blank', 'noopener')

      // plex.tv cannot call back to a local app, so poll until the code is approved.
      pollTimer.current = window.setInterval(async () => {
        try {
          const result = await api.plexPinStatus(started.id)
          if (!result.linked) return
          stopPolling()
          setPin(null)
          onConnectionChange(result.connection)
          if (result.servers?.length) setServers(result.servers)
          if (result.connection.url) setUrl(result.connection.url)
          notify('Signed in to Plex')
        } catch {
          stopPolling()
          setPin(null)
        }
      }, 2000)
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function useManualToken() {
    try {
      onConnectionChange(await api.setPlexToken(manualToken.trim()))
      setManualToken('')
      notify('Token saved for this session')
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function signOut() {
    stopPolling()
    setServers([])
    setPin(null)
    try {
      onConnectionChange(await api.resetConnections())
      notify('Signed out')
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function testPlex() {
    setTesting(true)
    try {
      onConnectionChange(await api.plexTest(url))
    } catch (e) {
      // The backend records the failure on the session; refresh so it renders.
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
      try {
        onConnectionChange(await api.connections(config))
      } catch {
        /* leave the previous state visible */
      }
    } finally {
      setTesting(false)
    }
  }

  async function testTmdb() {
    setTestingTmdb(true)
    try {
      onConnectionChange(await api.tmdbTest(apikey))
      notify('TMDb key accepted')
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
      try {
        onConnectionChange(await api.connections(config))
      } catch {
        /* leave the previous state visible */
      }
    } finally {
      setTestingTmdb(false)
    }
  }

  async function saveToConfig() {
    if (!config) return
    setSaving(true)
    try {
      const result = await api.saveConnections(config)
      notify(
        result.written.length
          ? `Saved ${result.written.join(', ')} to ${config}`
          : 'Nothing to save yet',
      )
      onConfigChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    } finally {
      setSaving(false)
    }
  }

  async function addLibrary(library: PlexLibrary) {
    if (!config) return
    try {
      await api.setValue(config, ['libraries', library.name], {
        library_type: library.libraryType,
        collection_files: [],
      })
      notify(`Added ${library.name} to ${config}`)
      onConfigChanged()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  const signedIn = Boolean(connection?.hasToken)
  const libraries = connection?.libraries ?? []

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-5">
        <Section
          title="Plex"
          hint="Kometa needs a server address and an admin token. KometaUI only ever reads from Plex."
        >
          <Row label="Account">
            {signedIn ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-ok">✓ Token held</span>
                {connection?.tokenFromConfig && (
                  <span className="text-ink-500">(read from {config ?? 'your config'})</span>
                )}
                <button
                  type="button"
                  onClick={signOut}
                  className="rounded border border-ink-700 px-2 py-0.5 text-ink-300 hover:bg-ink-800"
                >
                  Forget token
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={signIn}
                    className="rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110"
                  >
                    Sign in with Plex
                  </button>
                  {pin && (
                    // A "strong" PIN is a long random string carried in the auth URL
                    // rather than something to type, so point at the tab instead.
                    <span className="text-ink-400">
                      Approve access in the Plex tab that opened — waiting…{' '}
                      <a
                        href={pin.authUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent hover:underline"
                      >
                        reopen
                      </a>
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <input
                    value={manualToken}
                    onChange={(e) => setManualToken(e.target.value)}
                    type="password"
                    spellCheck={false}
                    placeholder="…or paste an existing X-Plex-Token"
                    className={inputClass}
                  />
                  <button
                    type="button"
                    onClick={useManualToken}
                    disabled={!manualToken.trim()}
                    className="shrink-0 rounded border border-ink-700 px-2 py-1 text-ink-200 hover:bg-ink-800 disabled:opacity-40"
                  >
                    Use
                  </button>
                </div>
              </div>
            )}
          </Row>

          <Row label="Server URL">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              spellCheck={false}
              placeholder="http://localhost:32400"
              className={inputClass}
            />
            {servers.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {servers.flatMap((server) =>
                  server.connections.slice(0, 3).map((c) => (
                    <button
                      key={`${server.name}-${c.uri}`}
                      type="button"
                      onClick={() => setUrl(c.uri)}
                      className="rounded border border-ink-700 px-1.5 py-0.5 text-[11px] text-ink-300 hover:bg-ink-800"
                      title={`${server.name}${c.relay ? ' (relay)' : c.local ? ' (local)' : ''}`}
                    >
                      {c.uri}
                    </button>
                  )),
                )}
              </div>
            )}
          </Row>

          <Row label="">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={testPlex}
                disabled={!signedIn || !url || testing}
                className="rounded border border-ink-700 px-3 py-1 text-ink-200 hover:bg-ink-800 disabled:opacity-40"
              >
                {testing ? 'Testing…' : 'Test connection'}
              </button>
              {connection?.serverName && (
                <span className="text-ok">
                  ✓ {connection.serverName} · Plex {connection.serverVersion}
                </span>
              )}
              {connection?.plexError && <span className="text-danger">✕ {connection.plexError}</span>}
            </div>
          </Row>
        </Section>

        {libraries.length > 0 && (
          <Section
            title={`Libraries on ${connection?.serverName ?? 'this server'}`}
            hint="Names must match your config exactly. Adding one writes it with the right library_type."
          >
            <div className="space-y-1.5">
              {libraries.map((library) => (
                <div
                  key={library.key}
                  className="flex items-center gap-2 rounded border border-ink-800 bg-ink-900 px-2.5 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-ink-100">{library.name}</span>
                  <span className="rounded border border-ink-700 px-1.5 py-0.5 text-[10px] uppercase text-ink-400">
                    {library.libraryType}
                  </span>
                  <button
                    type="button"
                    onClick={() => addLibrary(library)}
                    disabled={!canWrite || !config}
                    className="rounded bg-accent px-2 py-0.5 text-[11px] text-ink-950 hover:brightness-110 disabled:opacity-30"
                  >
                    Add to config
                  </button>
                </div>
              ))}
            </div>
          </Section>
        )}

        <Section title="TMDb" hint="Required. Kometa will not start without a working API key.">
          <Row label="API key">
            <input
              value={apikey}
              onChange={(e) => setApikey(e.target.value)}
              spellCheck={false}
              type="password"
              placeholder={connection?.hasApikey ? 'A key is already held' : ''}
              className={inputClass}
            />
          </Row>
          <Row label="">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={testTmdb}
                disabled={!apikey || testingTmdb}
                className="rounded border border-ink-700 px-3 py-1 text-ink-200 hover:bg-ink-800 disabled:opacity-40"
              >
                {testingTmdb ? 'Testing…' : 'Test key'}
              </button>
              {connection?.tmdbOk && <span className="text-ok">✓ Key accepted</span>}
              {connection?.tmdbError && <span className="text-danger">✕ {connection.tmdbError}</span>}
            </div>
          </Row>
        </Section>

        <div className="flex items-center gap-3 border-t border-ink-800 pt-4">
          <span className="text-ink-500">
            {config ? `Writes to ${config}` : 'No config selected'}
          </span>
          <button
            type="button"
            onClick={saveToConfig}
            disabled={!config || !canWrite || saving}
            className="ml-auto rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110 disabled:opacity-30"
            title={!canWrite ? 'Unlock writes to save' : undefined}
          >
            {saving ? 'Saving…' : 'Save to config'}
          </button>
        </div>
      </div>
    </div>
  )
}

const inputClass =
  'w-full rounded border border-ink-700 bg-ink-900 px-2 py-1 font-mono text-ink-100 outline-none placeholder:text-ink-600 focus:border-accent'

function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section>
      <h2 className="text-ink-100">{title}</h2>
      {hint && <p className="mb-2 mt-0.5 text-ink-500">{hint}</p>}
      <div className="space-y-2">{children}</div>
    </section>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-start gap-3">
      <span className="pt-1 text-ink-400">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  )
}
