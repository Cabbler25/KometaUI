import { useState } from 'react'
import type { FileNode } from '../lib/api'

interface Props {
  node: FileNode
  activePath: string | null
  dirtyPaths: Set<string>
  errorPaths: Set<string>
  referencedPaths: Set<string>
  onSelect: (path: string) => void
  depth?: number
}

export function FileTree({
  node,
  activePath,
  dirtyPaths,
  errorPaths,
  referencedPaths,
  onSelect,
  depth = 0,
}: Props) {
  // The root node is a container for the workspace itself, not a row.
  if (depth === 0) {
    return (
      <div className="py-1">
        {node.children.map((child) => (
          <FileTree
            key={child.path}
            node={child}
            activePath={activePath}
            dirtyPaths={dirtyPaths}
            errorPaths={errorPaths}
            referencedPaths={referencedPaths}
            onSelect={onSelect}
            depth={1}
          />
        ))}
      </div>
    )
  }

  return node.isDir ? (
    <Directory
      node={node}
      activePath={activePath}
      dirtyPaths={dirtyPaths}
      errorPaths={errorPaths}
      referencedPaths={referencedPaths}
      onSelect={onSelect}
      depth={depth}
    />
  ) : (
    <FileRow
      node={node}
      active={activePath === node.path}
      dirty={dirtyPaths.has(node.path)}
      hasError={errorPaths.has(node.path)}
      referenced={referencedPaths.has(node.path)}
      onSelect={onSelect}
      depth={depth}
    />
  )
}

function Directory({ node, depth, ...rest }: Props & { depth: number }) {
  const [open, setOpen] = useState(depth < 2)
  const indent = { paddingLeft: `${depth * 12 + 6}px` }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={indent}
        className="flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-ink-300 hover:bg-ink-800 hover:text-ink-100"
      >
        <span className="w-3 shrink-0 text-[9px] text-ink-400">{open ? '▼' : '▶'}</span>
        <span className="truncate">{node.name}</span>
      </button>
      {open &&
        node.children.map((child) => (
          <FileTree key={child.path} node={child} depth={depth + 1} {...rest} />
        ))}
    </>
  )
}

function FileRow({
  node,
  active,
  dirty,
  hasError,
  referenced,
  onSelect,
  depth,
}: {
  node: FileNode
  active: boolean
  dirty: boolean
  hasError: boolean
  referenced: boolean
  onSelect: (path: string) => void
  depth: number
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(node.path)}
      style={{ paddingLeft: `${depth * 12 + 21}px` }}
      className={`flex w-full items-center gap-1.5 py-[3px] pr-2 text-left ${
        active ? 'bg-accent-dim/40 text-ink-100' : 'text-ink-300 hover:bg-ink-800 hover:text-ink-100'
      }`}
      title={
        referenced
          ? `${node.path} — referenced by your config`
          : `${node.path} — present but not referenced by the selected config`
      }
    >
      <span className="truncate">{node.name}</span>
      {/* A file sitting in the folder but not referenced by config.yml is never read by
          Kometa; that is worth showing, since it is a common source of confusion. */}
      {!referenced && <span className="shrink-0 text-[9px] text-ink-600">unused</span>}
      {hasError && <span className="shrink-0 text-danger">●</span>}
      {dirty && <span className="shrink-0 text-warn">●</span>}
    </button>
  )
}
