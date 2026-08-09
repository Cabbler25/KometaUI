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
}
