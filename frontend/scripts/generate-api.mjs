import { execFileSync } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const openapiUrl = new URL('/openapi.json', baseUrl).toString()
const response = await fetch(openapiUrl)
if (!response.ok) throw new Error(`OpenAPI request failed (${response.status}) at ${openapiUrl}`)

const temporaryDirectory = await mkdtemp(join(tmpdir(), 'arbiter-openapi-'))
const inputPath = join(temporaryDirectory, 'openapi.json')
try {
  await writeFile(inputPath, await response.text(), 'utf8')
  execFileSync(process.execPath, ['node_modules/openapi-typescript/bin/cli.js', inputPath, '-o', 'lib/generated-api-types.ts'], { stdio: 'inherit' })
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true })
}
