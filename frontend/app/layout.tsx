import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import './globals.css'
import { QueryProvider } from '@/components/query-provider'

export const metadata: Metadata = {
  title: 'Arbiter — Reconciliation that knows when not to guess',
  description: 'Evidence-backed reconciliation for the transactions that do not reconcile themselves.',
  generator: 'v0.app',
}

export const viewport: Viewport = { colorScheme: 'dark', themeColor: '#08090b' }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" className="bg-background"><body className="antialiased"><QueryProvider>{children}</QueryProvider>{process.env.NODE_ENV === 'production' && <Analytics />}</body></html>
}
