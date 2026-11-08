import {build} from 'esbuild'
import {copyFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
const root=fileURLToPath(new URL('../',import.meta.url))
await build({entryPoints:[root+'scripts/render-report.jsx'],outfile:root+'dist/report-renderer.cjs',bundle:true,platform:'node',format:'cjs',jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'}})
await copyFile(root+'src/style.css',root+'dist/report-base.css')
await copyFile(root+'src/decision.css',root+'dist/report-decision.css')
await copyFile(root+'src/report-print.css',root+'dist/report-print.css')
console.log('Shared DecisionReport renderer and styles built.')
