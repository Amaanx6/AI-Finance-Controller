import Link from 'next/link'

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" width={size} height={size} focusable="false">
        <path d="M8 22.5 16 7l8 15.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M11.5 18h9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="8" cy="22.5" r="2" fill="currentColor" />
        <circle cx="16" cy="7" r="2" fill="currentColor" />
        <circle cx="24" cy="22.5" r="2" fill="currentColor" />
      </svg>
    </span>
  )
}

export function Brand({ href = '/', compact = false }: { href?: string; compact?: boolean }) {
  return <Link href={href} className="brand"><BrandMark /><span className={compact ? 'sr-only' : undefined}>arbiter</span></Link>
}
