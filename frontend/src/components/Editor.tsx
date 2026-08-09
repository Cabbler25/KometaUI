import { useEffect, useRef } from 'react'
import { getModel, monaco, setupMonaco } from '../lib/monaco'

interface Props {
  path: string
  kind: string | null
  text: string
  readOnly: boolean
  revealLine: number | null
  onChange: (text: string) => void
  onSave: () => void
}

export function Editor({ path, kind, text, readOnly, revealLine, onChange, onSave }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)
  // Held in a ref so the Cmd/Ctrl+S command always calls the current handler without
  // needing to be re-registered (Monaco has no way to replace a keybinding).
  const saveHandler = useRef(onSave)
  saveHandler.current = onSave

  useEffect(() => {
    setupMonaco()
    if (!host.current) return

    const instance = monaco.editor.create(host.current, {
      theme: 'kometa-dark',
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 13,
      fontFamily: 'ui-monospace, "Cascadia Code", Consolas, monospace',
      lineNumbersMinChars: 4,
      scrollBeyondLastLine: false,
      renderWhitespace: 'selection',
      tabSize: 2,
      insertSpaces: true,
      padding: { top: 10, bottom: 40 },
      quickSuggestions: { other: true, comments: false, strings: true },
    })
    editor.current = instance

    instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveHandler.current())

    return () => {
      instance.dispose()
      editor.current = null
    }
  }, [])

  // Swap the model when the open file — or its detected kind — changes. Changing kind
  // changes the URI, which is how the correct schema gets attached.
  useEffect(() => {
    const instance = editor.current
    if (!instance) return

    const model = getModel(path, kind, text)
    if (instance.getModel() !== model) instance.setModel(model)

    const subscription = model.onDidChangeContent(() => onChange(model.getValue()))
    return () => subscription.dispose()
    // `text` is deliberately excluded: it flows in through getModel on file switch, and
    // re-running on every keystroke would fight the user's cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, kind])

  useEffect(() => {
    editor.current?.updateOptions({ readOnly })
  }, [readOnly])

  useEffect(() => {
    if (revealLine == null || !editor.current) return
    editor.current.revealLineInCenter(revealLine)
    editor.current.setPosition({ lineNumber: revealLine, column: 1 })
    editor.current.focus()
  }, [revealLine])

  return <div ref={host} className="h-full w-full" />
}
