/**
 * monaco-editor ships no types for its individual basic-language definitions, only for
 * the `.contribution` wrappers. We import the definition directly to avoid Vite's dep
 * optimiser mis-resolving the contribution's dynamic import (see src/lib/monaco.ts).
 */
declare module 'monaco-editor/esm/vs/basic-languages/yaml/yaml.js' {
  import type { languages } from 'monaco-editor/esm/vs/editor/editor.api.js'

  export const conf: languages.LanguageConfiguration
  export const language: languages.IMonarchLanguage
}
