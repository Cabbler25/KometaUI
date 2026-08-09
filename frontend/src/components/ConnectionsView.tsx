/**
 * Set up the connections Kometa needs, and confirm they work before it runs.
 *
 * Two things this fixes. Getting a Plex token normally means digging through an item's
 * XML for `X-Plex-Token`; here you approve a short code on plex.tv instead. And library
 * names in config.yml must match the server character for character — a typo produces a
 * silent no-op rather than an error — so the real names are read off the server and
 * written for you.
 *
 * Strictly read-only against Plex: this lists libraries and versions, and only ever
 * writes to your own config file.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  api,
  type PlexLibrary,
  type PlexPin,
  type PlexServer,
} from '../lib/api'

interface Props {
  config: string | null
  canWrite: boolean
  onLibrariesDiscovered: (libraries: PlexLibrary[]) => void
  onConfigChanged: () => void
  notify: (text: string, tone?: 'ok' | 'bad') => void
}

type Health = { state: 'idle' | 'checking' | 'ok' | 'bad'; message?: string }

export function ConnectionsView({
  config,
  canWrite,
  onLibrariesDiscovered,
  onConfigChanged,
  notify,
}: Props) {
  const [url, setUrl] = useState('http://localhost:32400')
  const [token, setToken] = useState('')
  const [apikey, setApikey] = useState('')
  const [plexHealth, setPlexHealth] = useState<Health>({ state: 'idle' })
  const [tmdbHealth, setTmdbHealth] = useState<Health>({ state: 'idle' })
  const [pin, setPin] = useState<PlexPin | null>(null)
  const [servers, setServers] = useState<PlexServer[]>([])
  const [libraries, setLibraries] = useState<PlexLibrary[]>([])
  const [saving, setSaving] = useState(false)
  const pollTimer = useRef<number | null>(null)

  // Prefill from the open config so this reads as "review and confirm", not "start over".
  useEffect(() => {
    if (!config) return
    api
      .readFile(config)
      .then(({ text }) => {
        setUrl(matchScalar(text, 'url') ?? 'http://localhost:32400')
        setToken(matchScalar(text, 'token') ?? '')
        setApikey(matchScalar(text, 'apikey') ?? '')
      })
      .catch(() => undefined)
  }, [config])

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

      // plex.tv has no callback to a local app, so poll until the code is approved.
      pollTimer.current = window.setInterval(async () => {
        try {
          const { linked, token: authToken } = await api.plexPinStatus(started.id)
          if (!linked || !authToken) return
          stopPolling()
          setPin(null)
          setToken(authToken)
          notify('Signed in to Plex')
          const { servers: found } = await api.plexServers(authToken)
          setServers(found)
          const firstLocal = found[0]?.connections[0]?.uri
          if (firstLocal) setUrl(firstLocal)
        } catch {
          stopPolling()
          setPin(null)
        }
      }, 2000)
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), 'bad')
    }
  }

  async function testPlex() {
    setPlexHealth({ state: 'checking' })
    try {
      const info = await api.plexTest(url, token)
      setPlexHealth({ state: 'ok', message: `${info.name} · Plex ${info.version}` })
      const { libraries: found } = await api.plexLibraries(url, token)
      setLibraries(found)
      onLibrariesDiscovered(found)
    } catch (e) {
      setPlexHealth({ state: 'bad', message: e instanceof ApiError ? e.message : String(e) })
      setLibraries([])
    }
  }

  async function testTmdb() {
    setTmdbHealth({ state: 'checking' })
    try {
      await api.tmdbTest(apikey)
      setTmdbHealth({ state: 'ok', message: 'Key accepted' })
    } catch (e) {
      setTmdbHealth({ state: 'bad', message: e instanceof ApiError ? e.message : String(e) })
    }
  }

  async function saveToConfig() {
    if (!config) return
    setSaving(true)
    try {
      // Written one value at a time so each lands as a surgical edit and the rest of the
      // file — comments included — is untouched.
      if (url) await api.setValue(config, ['plex', 'url'], url)
      if (token) await api.setValue(config, ['plex', 'token'], token)
      if (apikey) await api.setValue(config, ['tmdb', 'apikey'], apikey)
      notify(`Saved connection settings to ${config}`)
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

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-5">
        <Section
          title="Plex"
          hint="Kometa needs a server address and an admin token. KometaUI only ever reads from Plex."
        >
          <Row label="Sign in">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={signIn}
                className="rounded bg-accent px-3 py-1 font-medium text-ink-950 hover:brightness-110"
              >
                Sign in with Plex
              </button>
              {pin && (
                // A "strong" PIN is a long random string carried in the auth URL rather
                // than something to type, so point at the tab instead of showing it.
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
                  server.connections.slice(0, 3).map((connection) => (
                    <button
                      key={`${server.name}-${connection.uri}`}
                      type="button"
                      onClick={() => setUrl(connection.uri)}
                      className="rounded border border-ink-700 px-1.5 py-0.5 text-[11px] text-ink-300 hover:bg-ink-800"
                      title={`${server.name}${connection.relay ? ' (relay)' : connection.local ? ' (local)' : ''}`}
                    >
                      {connection.uri}
                    </button>
                  )),
                )}
              </div>
            )}
          </Row>

          <Row label="Token">
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              spellCheck={false}
              type="password"
              placeholder="Signed in above, or paste an existing token"
              className={inputClass}
            />
          </Row>

          <Row label="">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={testPlex}
                disabled={!url || !token || plexHealth.state === 'checking'}
                className="rounded border border-ink-700 px-3 py-1 text-ink-200 hover:bg-ink-800 disabled:opacity-40"
              >
                {plexHealth.state === 'checking' ? 'Testing…' : 'Test connection'}
              </button>
              <HealthBadge health={plexHealth} />
            </div>
          </Row>
        </Section>

        {libraries.length > 0 && (
          <Section
            title="Libraries on this server"
            hint="Names must match your config exactly. Add one to write it with the right library_type."
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
              className={inputClass}
            />
          </Row>
          <Row label="">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={testTmdb}
                disabled={!apikey || tmdbHealth.state === 'checking'}
                className="rounded border border-ink-700 px-3 py-1 text-ink-200 hover:bg-ink-800 disabled:opacity-40"
              >
                {tmdbHealth.state === 'checking' ? 'Testing…' : 'Test key'}
              </button>
              <HealthBadge health={tmdbHealth} />
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

function HealthBadge({ health }: { health: Health }) {
  if (health.state === 'idle') return null
  if (health.state === 'checking') return <span className="text-ink-500">Checking…</span>
  return (
    <span className={health.state === 'ok' ? 'text-ok' : 'text-danger'}>
      {health.state === 'ok' ? '✓ ' : '✕ '}
      {health.message}
    </span>
  )
}

/**
 * Pull a scalar out of raw config text for prefilling.
 *
 * Deliberately a regex rather than a parse: this runs on every config load just to
 * populate three inputs, and a malformed file should leave the fields blank rather than
 * throw. The authoritative read/write path is the backend's YAML handling.
 */
function matchScalar(text: string, key: string): string | null {
  const match = new RegExp(`^\\s*${key}:\\s*(.+?)\\s*$`, 'm').exec(text)
  if (!match) return null
  return match[1].replace(/^['"]|['"]$/g, '') || null
}
