/**
 * Monaco configured with Kometa's own JSON schemas.
 *
 * This is what makes the editor worth using: autocomplete, hover documentation, and
 * inline errors driven by the same schemas Kometa ships for VS Code — served from our
 * backend so they always match the Kometa version the catalog was generated from.
 *
 * Kometa decides a file's type from its *content*, not its name — `movies.yml` could be a
 * collection file, an overlay file, or a config. Monaco associates schemas by filename
 * glob, so we bridge the two by giving each open document a synthetic URI that encodes
 * the kind the backend detected: `file:///kometa/collection/movies.yml`.
 */

// Import the editor API directly rather than the `monaco-editor` root entry. The root
// entry registers every bundled language -- SQL, Solidity, PowerQuery and ~90 others --
// which we will never open. Pulling in just the API plus the YAML grammar cuts the
// bundle by roughly 1.3 MB.
//
// monaco-editor is pinned to 0.52.x on purpose. 0.53 reworked `createWebWorker` to take
// `{ worker, host }` instead of `{ moduleId, label, createData }`, and monaco-yaml's
// `monaco-worker-manager` dependency still passes the old shape. Under 0.56 the options
// are silently ignored, every YAML request lands on the plain editor worker, and the
// editor fails at runtime with "Missing requestHandler or method: doValidation" -- no
// build error, just no validation or completion. Re-check monaco-yaml's compatibility
// before bumping this.
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js'
// The grammar is registered by hand below rather than via
// `basic-languages/yaml/yaml.contribution.js`. That module registers a *lazy* loader
// (`loader: () => import('./yaml.js')`), and Vite's dependency optimiser resolves that
// dynamic specifier to the npm `yaml` package — a transitive dependency of monaco-yaml
// with the same name — instead of monaco's own yaml.js. The import then 404s, YAML never
// finishes registering, and the language service never starts. Importing the definition
// statically sidesteps the collision entirely.
import { conf as yamlConf, language as yamlLanguage } from 'monaco-editor/esm/vs/basic-languages/yaml/yaml.js'
import { configureMonacoYaml } from 'monaco-yaml'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import YamlWorker from 'monaco-yaml/yaml.worker?worker'

import { api } from './api'

export type KometaKind = 'config' | 'collection' | 'overlay' | 'metadata' | 'playlist' | 'template'

const SCHEMA_FILE: Record<KometaKind, string> = {
  config: 'config-schema.json',
  collection: 'collection-schema.json',
  overlay: 'overlay-schema.json',
  metadata: 'metadata-schema.json',
  playlist: 'playlist-schema.json',
  template: 'template-schema.json',
}

let initialised = false

export function setupMonaco() {
  if (initialised) return
  initialised = true

  window.MonacoEnvironment = {
    getWorker(_moduleId, label) {
      return label === 'yaml' ? new YamlWorker() : new EditorWorker()
    },
  }

  monaco.languages.register({
    id: 'yaml',
    extensions: ['.yaml', '.yml'],
    aliases: ['YAML', 'yaml'],
    mimetypes: ['application/x-yaml', 'text/x-yaml'],
  })
  monaco.languages.setLanguageConfiguration('yaml', yamlConf)
  monaco.languages.setMonarchTokensProvider('yaml', yamlLanguage)

  configureMonacoYaml(monaco, {
    enableSchemaRequest: true,
    validate: true,
    hover: true,
    completion: true,
    // Formatting stays off: the editor must never rewrite the user's layout.
    format: { singleQuote: false },
    schemas: (Object.keys(SCHEMA_FILE) as KometaKind[]).map((kind) => ({
      uri: new URL(api.schemaUrl(SCHEMA_FILE[kind]), window.location.origin).href,
      // Matches the synthetic URIs minted by modelUriFor().
      fileMatch: [`**/kometa/${kind}/**`],
    })),
  })

  monaco.editor.defineTheme('kometa-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#12131a',
      'editorGutter.background': '#12131a',
      'editorLineNumber.foreground': '#4b5169',
      'editorLineNumber.activeForeground': '#a9b1d6',
    },
  })
}

/**
 * Build the synthetic URI that binds a document to its schema.
 *
 * Falls back to a `unknown/` segment when the backend could not classify the file, which
 * matches no schema — better to offer no completions than the wrong ones.
 */
export function modelUriFor(path: string, kind: string | null): monaco.Uri {
  return monaco.Uri.parse(`file:///kometa/${kind ?? 'unknown'}/${path}`)
}

/** Get or create the model for a file, replacing it if its detected kind changed. */
export function getModel(path: string, kind: string | null, text: string): monaco.editor.ITextModel {
  const uri = modelUriFor(path, kind)
  const existing = monaco.editor.getModel(uri)
  if (existing) {
    if (existing.getValue() !== text) existing.setValue(text)
    return existing
  }
  // A file whose kind changed (adding `collections:` to an empty file, say) needs a new
  // URI, so retire any stale model for the same path under a different kind.
  for (const model of monaco.editor.getModels()) {
    if (model.uri.path.endsWith(`/${path}`) && model.uri.toString() !== uri.toString()) {
      model.dispose()
    }
  }
  return monaco.editor.createModel(text, 'yaml', uri)
}

export { monaco }
