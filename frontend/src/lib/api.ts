/** Typed client for the KometaUI backend. */

export type Severity = 'error' | 'warning'
export type Engine = 'kometa' | 'schema' | 'syntax'

export interface Finding {
  message: string
  severity: Severity
  engine: Engine
  path: string
  line: number | null
  column: number | null
}

export interface ValidationResult {
  file: string
  kind: string | null
  engine: Engine
  ok: boolean
  findings: Finding[]
}

export interface FileNode {
  name: string
  path: string
  isDir: boolean
  size: number | null
  children: FileNode[]
}

export interface ConfigCandidate {
  path: string
  libraries: string[]
  modified: number
  size: number
  isConventionalDefault: boolean
}

export interface FileReference {
  kind: string
  value: string
  library: string | null
  listKey: string
  resolved: string | null
  /** Workspace-relative path, present only when the file is inside the workspace. */
  relative: string | null
  exists: boolean | null
  templateVariables: Record<string, unknown> | null
}

export interface Status {
  workspace: { path: string; name: string; allowWrites: boolean } | null
  catalog: {
    kometaVersion: string | null
    builderCount: number | null
    schemaGapCount: number | null
    loaded: boolean
  }
  validationEngine: {
    kometa_available: boolean
    kometa_source: string | null
    kometa_version: string | null
    detail: string | null
  }
}

export type Control =
  | 'text'
  | 'textarea'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'select'
  | 'multiselect'
  | 'list'
  | 'object'
  | 'yaml'

export interface FormField {
  name: string
  label: string
  control: Control
  description: string
  required: boolean
  default: unknown
  options: unknown[]
  minimum: number | null
  maximum: number | null
  fields: FormField[]
  item_control: Control | null
  placeholder: string
}

export interface EnabledDefault {
  name: string
  kind: 'collection' | 'overlay' | 'playlist'
  library: string | null
  listKey: string
  index: number
  legacyKey: boolean
  templateVariables: Record<string, unknown>
}

export interface DefaultsGroup {
  names: string[]
  files: Record<string, string>
  declared_but_missing: string[]
  template_variable_refs: Record<string, string>
  shared_template_variable_ref: string | null
}

export interface BuilderDocs {
  hint: string
  examples: string[]
}

export interface Catalog {
  kometa_version: string
  services: Record<string, { label: string; builders: string[] }>
  builder_groups: Record<string, string[]>
  detail_groups: Record<string, string[]>
  defaults: Record<'collections' | 'overlays' | 'playlists', DefaultsGroup>
  collection_property_descriptions: Record<string, string>
  builders_missing_from_schema: string[]
  builder_examples: Record<string, BuilderDocs>
}

export interface EditResult {
  path: string
  changed: boolean
  text: string
  backup?: string | null
  validation?: ValidationResult
}

export interface PlexPin {
  id: number
  code: string
  authUrl: string
}

export interface PlexServer {
  name: string
  product: string
  version: string
  owned: boolean
  connections: { uri: string; local: boolean; relay: boolean }[]
}

export interface PlexLibrary {
  name: string
  plexType: string
  /** The value Kometa expects for `library_type`. */
  libraryType: string
  key: string
}

/**
 * Server-held connection state. Note there is no token field: the backend keeps it and
 * only reports whether one is held, so it never reaches the browser.
 */
export interface ConnectionState {
  url: string | null
  hasToken: boolean
  tokenFromConfig: boolean
  hasApikey: boolean
  serverName: string | null
  serverVersion: string | null
  libraries: PlexLibrary[]
  plexError: string | null
  tmdbOk: boolean | null
  tmdbError: string | null
}

export interface PreviewItem {
  title: string
  year: number | null
  type: string | null
  ratingKey: string
  thumb: string | null
}

export interface PreviewResult {
  total: number
  items: PreviewItem[]
  /** Conditions that were translated and applied. */
  applied: string[]
  /** Conditions that could not be translated — the count is incomplete when non-empty. */
  skipped: { condition: string; reason: string }[]
  truncated: boolean
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }

  /** A locked workspace is an expected state, not a failure — the UI offers to unlock. */
  get isReadOnly() {
    return this.status === 423
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      /* non-JSON error body; keep the status text */
    }
    throw new ApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

export const api = {
  status: () => request<Status>('/api/status'),

  openWorkspace: (path: string, allowWrites = false) =>
    request<{ path: string; name: string; allowWrites: boolean; configs: ConfigCandidate[] }>(
      '/api/workspace/open',
      { method: 'POST', body: JSON.stringify({ path, allow_writes: allowWrites }) },
    ),

  setWrites: (allow: boolean) =>
    request<{ allowWrites: boolean }>('/api/workspace/writes', {
      method: 'POST',
      body: JSON.stringify({ allow }),
    }),

  tree: () => request<FileNode>('/api/workspace/tree'),

  configs: () => request<{ configs: ConfigCandidate[] }>('/api/workspace/configs'),

  references: (config: string) =>
    request<{ config: string; references: FileReference[]; missing: string[] }>(
      `/api/workspace/references?config=${encodeURIComponent(config)}`,
    ),

  readFile: (path: string) =>
    request<{ path: string; text: string; validation: ValidationResult }>(
      `/api/files?path=${encodeURIComponent(path)}`,
    ),

  writeFile: (path: string, text: string) =>
    request<{ path: string; backup: string | null; validation: ValidationResult }>(
      `/api/files?path=${encodeURIComponent(path)}`,
      { method: 'PUT', body: JSON.stringify({ text }) },
    ),

  validate: (text: string, filename: string) =>
    request<ValidationResult>('/api/validate', {
      method: 'POST',
      body: JSON.stringify({ text, filename }),
    }),

  validateAll: () =>
    request<{
      results: ValidationResult[]
      summary: { files: number; withErrors: number; findings: number }
    }>('/api/validate/all', { method: 'POST' }),

  schemaUrl: (name: string) => `/api/schemas/${name}`,

  catalog: () => request<Catalog>('/api/catalog'),

  // -- forms ---------------------------------------------------------------------

  formModel: (schema: string, definition: string) =>
    request<{ fields: FormField[] }>(`/api/forms/${schema}/${definition}`),

  defaultForm: (kind: string, name: string) =>
    request<{ name: string; definition: string; file: string | null; fields: FormField[] }>(
      `/api/forms/defaults/${kind}/${encodeURIComponent(name)}`,
    ),

  builderForm: (builder: string) =>
    request<{
      builder: string
      service: string | null
      inSchema: boolean
      field: FormField
      /** One-line summary from Kometa's example galleries. */
      hint: string
      /** Worked YAML snippets, fullest first. */
      examples: string[]
    }>(`/api/forms/builder/${encodeURIComponent(builder)}`),

  // -- structured edits ------------------------------------------------------------

  enabledDefaults: (config: string, library?: string) =>
    request<{ enabled: EnabledDefault[] }>(
      `/api/defaults/enabled?config=${encodeURIComponent(config)}` +
        (library ? `&library=${encodeURIComponent(library)}` : ''),
    ),

  addDefault: (
    config: string,
    library: string,
    kind: string,
    name: string,
    templateVariables: Record<string, unknown> = {},
  ) =>
    request<EditResult>('/api/defaults/add', {
      method: 'POST',
      body: JSON.stringify({ config, library, kind, name, template_variables: templateVariables }),
    }),

  removeDefault: (config: string, library: string, listKey: string, index: number) =>
    request<EditResult>('/api/defaults/remove', {
      method: 'POST',
      body: JSON.stringify({ config, library, list_key: listKey, index }),
    }),

  setTemplateVariables: (
    config: string,
    library: string,
    listKey: string,
    index: number,
    templateVariables: Record<string, unknown>,
  ) =>
    request<EditResult>('/api/defaults/template-variables', {
      method: 'POST',
      body: JSON.stringify({
        config,
        library,
        list_key: listKey,
        index,
        template_variables: templateVariables,
      }),
    }),

  addDefinition: (
    kind: 'collection' | 'overlay',
    path: string,
    name: string,
    definition: Record<string, unknown>,
  ) =>
    request<EditResult>(kind === 'overlay' ? '/api/overlays/add' : '/api/collections/add', {
      method: 'POST',
      body: JSON.stringify({ path, name, definition }),
    }),

  setValue: (path: string, pointer: (string | number)[], value: unknown) =>
    request<EditResult>('/api/documents/set', {
      method: 'POST',
      body: JSON.stringify({ path, pointer, value }),
    }),

  readValue: (path: string, pointer: string) =>
    request<{ exists: boolean; value: unknown }>(
      `/api/documents/value?path=${encodeURIComponent(path)}&pointer=${encodeURIComponent(pointer)}`,
    ),

  /** Reconcile a mapping to `values`, one surgical edit per changed key. */
  mergeMapping: (path: string, pointer: (string | number)[], values: Record<string, unknown>) =>
    request<EditResult>('/api/documents/merge', {
      method: 'POST',
      body: JSON.stringify({ path, pointer, values }),
    }),

  // -- connections (read-only) -------------------------------------------------------

  /** Current connection state. Pass the config to seed from an already-saved token. */
  connections: (config?: string | null) =>
    request<ConnectionState>(
      `/api/connections${config ? `?config=${encodeURIComponent(config)}` : ''}`,
    ),

  setPlexToken: (token: string) =>
    request<ConnectionState>('/api/connections/token', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),

  resetConnections: () => request<ConnectionState>('/api/connections/reset', { method: 'POST' }),

  plexPin: () => request<PlexPin>('/api/plex/pin', { method: 'POST' }),

  /** The token is stored server-side on success and never returned here. */
  plexPinStatus: (id: number) =>
    request<{ linked: boolean; servers?: PlexServer[]; connection: ConnectionState }>(
      `/api/plex/pin/${id}`,
    ),

  plexServers: () => request<{ servers: PlexServer[] }>('/api/plex/servers', { method: 'POST' }),

  /** Verifies the address and discovers libraries in one call. */
  plexTest: (url?: string) =>
    request<ConnectionState>('/api/plex/test', {
      method: 'POST',
      body: JSON.stringify({ url: url ?? null }),
    }),

  tmdbTest: (apikey: string) =>
    request<ConnectionState>('/api/tmdb/test', {
      method: 'POST',
      body: JSON.stringify({ apikey }),
    }),

  /** Parse a YAML snippet server-side and return the value under `key`. */
  parseSnippet: (text: string, key?: string) =>
    request<{ value: unknown }>('/api/yaml/parse', {
      method: 'POST',
      body: JSON.stringify({ text, key: key ?? null }),
    }),

  preview: (library: string, definition: Record<string, unknown>) =>
    request<PreviewResult>('/api/preview', {
      method: 'POST',
      body: JSON.stringify({ library, definition }),
    }),

  previewSupported: (definition: Record<string, unknown>) =>
    request<{ previewable: boolean; blocking: string[] }>('/api/preview/supported', {
      method: 'POST',
      body: JSON.stringify({ library: '', definition }),
    }),

  saveConnections: (config: string) =>
    request<EditResult & { written: string[] }>('/api/connections/save', {
      method: 'POST',
      body: JSON.stringify({ config }),
    }),
}
