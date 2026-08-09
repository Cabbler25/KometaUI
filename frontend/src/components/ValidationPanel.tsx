import type { Finding, ValidationResult } from '../lib/api'

interface Props {
  result: ValidationResult | null
  workspaceResults: ValidationResult[] | null
  busy: boolean
  onValidateAll: () => void
  onOpenFinding: (file: string, line: number | null) => void
  onClearWorkspaceResults: () => void
}

const ENGINE_LABEL: Record<string, string> = {
  kometa: "Kometa's validator",
  schema: 'Bundled schema',
  syntax: 'YAML parser',
}

export function ValidationPanel({
  result,
  workspaceResults,
  busy,
  onValidateAll,
  onOpenFinding,
  onClearWorkspaceResults,
}: Props) {
  const showingWorkspace = workspaceResults !== null

  return (
    <div className="flex h-full flex-col bg-ink-900">
      <div className="flex shrink-0 items-center gap-3 border-b border-ink-800 px-3 py-1.5">
        <span className="font-medium text-ink-200">Problems</span>
        {showingWorkspace ? (
          <WorkspaceSummary results={workspaceResults} />
        ) : (
          result && <FileSummary result={result} />
        )}
        <div className="ml-auto flex items-center gap-2">
          {showingWorkspace && (
            <button
              type="button"
              onClick={onClearWorkspaceResults}
              className="rounded px-2 py-0.5 text-ink-400 hover:bg-ink-800 hover:text-ink-200"
            >
              Show current file
            </button>
          )}
          <button
            type="button"
            onClick={onValidateAll}
            disabled={busy}
            className="rounded bg-ink-800 px-2 py-0.5 text-ink-200 hover:bg-ink-700 disabled:opacity-50"
          >
            {busy ? 'Validating…' : 'Validate workspace'}
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {showingWorkspace ? (
          <WorkspaceFindings results={workspaceResults} onOpen={onOpenFinding} />
        ) : (
          <FileFindings result={result} onOpen={onOpenFinding} />
        )}
      </div>
    </div>
  )
}

function FileSummary({ result }: { result: ValidationResult }) {
  const errors = result.findings.filter((f) => f.severity === 'error').length
  const warnings = result.findings.length - errors
  return (
    <span className="flex items-center gap-2 text-ink-400">
      {errors > 0 && <span className="text-danger">{errors} error{errors === 1 ? '' : 's'}</span>}
      {warnings > 0 && <span className="text-warn">{warnings} warning{warnings === 1 ? '' : 's'}</span>}
      {result.findings.length === 0 && <span className="text-ok">No problems</span>}
      {result.kind && <Badge>{result.kind}</Badge>}
      <Badge>{ENGINE_LABEL[result.engine] ?? result.engine}</Badge>
    </span>
  )
}

function WorkspaceSummary({ results }: { results: ValidationResult[] }) {
  const bad = results.filter((r) => !r.ok).length
  return (
    <span className="text-ink-400">
      {results.length} files · {bad === 0 ? <span className="text-ok">all clean</span> : `${bad} with errors`}
    </span>
  )
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-ink-700 px-1.5 py-px text-[10px] uppercase tracking-wide text-ink-400">
      {children}
    </span>
  )
}

function FileFindings({
  result,
  onOpen,
}: {
  result: ValidationResult | null
  onOpen: (file: string, line: number | null) => void
}) {
  if (!result) return <Empty>Open a file to see its problems.</Empty>
  if (result.findings.length === 0) return <Empty>No problems found in this file.</Empty>
  return (
    <ul>
      {result.findings.map((finding, i) => (
        <FindingRow key={i} finding={finding} onClick={() => onOpen(result.file, finding.line)} />
      ))}
    </ul>
  )
}

function WorkspaceFindings({
  results,
  onOpen,
}: {
  results: ValidationResult[]
  onOpen: (file: string, line: number | null) => void
}) {
  const failing = results.filter((r) => r.findings.length > 0)
  if (failing.length === 0) return <Empty>Every file in the workspace is clean.</Empty>
  return (
    <ul>
      {failing.map((result) => (
        <li key={result.file}>
          <div className="sticky top-0 flex items-center gap-2 bg-ink-850 px-3 py-1 text-ink-200">
            <span className="truncate font-medium">{result.file}</span>
            <span className="text-ink-500">{result.findings.length}</span>
            <span className="ml-auto text-[10px] uppercase tracking-wide text-ink-500">
              {ENGINE_LABEL[result.engine] ?? result.engine}
            </span>
          </div>
          <ul>
            {result.findings.map((finding, i) => (
              <FindingRow
                key={i}
                finding={finding}
                onClick={() => onOpen(result.file, finding.line)}
              />
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )
}

function FindingRow({ finding, onClick }: { finding: Finding; onClick: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-start gap-2 px-3 py-1 text-left hover:bg-ink-800"
      >
        <span className={finding.severity === 'error' ? 'text-danger' : 'text-warn'}>
          {finding.severity === 'error' ? '✕' : '⚠'}
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-ink-200">{finding.message}</span>
          {finding.path && <span className="ml-2 text-ink-500">at {finding.path}</span>}
        </span>
        {finding.line != null && <span className="shrink-0 text-ink-500">:{finding.line}</span>}
      </button>
    </li>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-6 text-center text-ink-500">{children}</div>
}
