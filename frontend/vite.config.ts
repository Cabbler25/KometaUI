import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const BACKEND_PROXY = {
  '/api': { target: 'http://127.0.0.1:8770', changeOrigin: true },
  '/health': { target: 'http://127.0.0.1:8770', changeOrigin: true },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The backend owns file access and validation; Vite just forwards to it.
  server: {
    port: 5173,
    proxy: BACKEND_PROXY,
  },
  preview: {
    port: 5174,
    proxy: BACKEND_PROXY,
  },
  // Workers must be emitted as ES modules. monaco-yaml's yaml.worker pulls in CommonJS
  // dependencies (prettier, path-browserify, jsonc-parser); under the default `iife`
  // format those reach the browser unconverted and the worker dies on `module is not
  // defined`, after which monaco quietly degrades to a main-thread fallback that cannot
  // serve completions.
  worker: {
    format: 'es',
  },
  optimizeDeps: {
    // monaco-yaml itself must not be pre-bundled: doing so produces a second copy in
    // .vite/deps alongside the one `monaco-yaml/yaml.worker?worker` compiles from source.
    exclude: ['monaco-yaml'],
    // Its worker's transitive dependencies, however, must be. These six ship CommonJS or
    // UMD, and in dev Vite does not run dependency optimisation inside worker bundles —
    // so unconverted `module.exports` reaches the browser, the worker dies on "module is
    // not defined", and monaco silently degrades to a main-thread fallback that serves no
    // completions. Listing them here forces the ESM conversion the worker needs.
    // The production build bundles workers properly and does not depend on this.
    // Two of the worker's dependencies must stay out of this list:
    //   prettier - only reachable from monaco-yaml's optional formatter, and pre-bundling
    //     its Node-targeted CJS entry yields a module that fails to evaluate, blanking the
    //     page with no console error at all.
    //   yaml - collides with monaco's own YAML language contribution, which then resolves
    //     its dynamic import to .vite/deps/yaml-*.js (the npm package) instead of monaco's
    //     internal yaml.js, and fails to load.
    include: ['jsonc-parser', 'path-browserify', 'vscode-languageserver-types', 'vscode-uri'],
  },
  build: {
    chunkSizeWarningLimit: 3000,
  },
})
