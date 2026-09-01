// app/page.tsx
'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowDown, ArrowUpRight, Check, ChevronRight, CircleAlert, FileSearch, Menu, ShieldCheck, Sparkles } from 'lucide-react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import dynamic from 'next/dynamic'

const LiquidGlass = dynamic(() => import('liquid-glass-react'), { ssr: false })

const fade = {
  initial: { opacity: 0, y: 18 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, amount: 0.18 },
  transition: { duration: 0.7, ease: 'easeOut' as const },
}

function Glass({
  children,
  className = '',
  liquid = false,
  mouseContainer,
}: {
  children: React.ReactNode
  className?: string
  liquid?: boolean
  mouseContainer?: React.RefObject<HTMLElement | null>
}) {
  if (liquid) {
    return (
      <div className="investigation-stage">
        <LiquidGlass
          className={`glass liquid-shell ${className}`}
          displacementScale={52}
          blurAmount={0.11}
          saturation={124}
          aberrationIntensity={1.1}
          elasticity={0.18}
          cornerRadius={22}
          mouseContainer={mouseContainer}
          mode="standard"
        >
          {children}
        </LiquidGlass>
      </div>
    )
  }

  return <div className={`glass ${className}`}>{children}</div>
}

function Nav() {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <header className="nav-wrap">
      <div className="nav">
        <Link href="#top" className="brand">
          <span className="brand-mark">A</span>
          <span>arbiter</span>
        </Link>

        <nav className={`nav-links ${menuOpen ? 'nav-links-open' : ''}`}>
          <Link href="#system" onClick={() => setMenuOpen(false)}>How it works</Link>
          <Link href="#proof" onClick={() => setMenuOpen(false)}>Proof</Link>
          <Link href="/runs" onClick={() => setMenuOpen(false)}>
            Open control room <ArrowUpRight size={14} />
          </Link>
        </nav>

        <Link className="nav-cta" href="/runs">
          Run a reconciliation <ChevronRight size={15} />
        </Link>

        <button
          className="menu"
          aria-label={menuOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
          type="button"
        >
          {menuOpen ? <span aria-hidden="true">×</span> : <Menu size={20} />}
        </button>
      </div>
    </header>
  )
}

function InvestigationVisual({
  mouseContainer,
}: {
  mouseContainer: React.RefObject<HTMLElement | null>
}) {
  const [stage, setStage] = useState(0)
  const reduced = useReducedMotion()

  useEffect(() => {
    if (reduced) {
      setStage(4)
      return
    }

    const timers = [700, 1500, 2600, 3900].map((delay, index) =>
      window.setTimeout(() => setStage(index + 1), delay),
    )

    return () => timers.forEach(window.clearTimeout)
  }, [reduced])

  const stages = ['AMBIGUOUS', 'PROPOSER', 'VERIFIER', 'RETRY', 'CONFIRMED']

  return (
    <Glass liquid mouseContainer={mouseContainer} className="investigation">
      <div className="visual-top">
        <span className="eyebrow">LIVE INVESTIGATION / 00428</span>
        <span className="status-dot">● {stage === 4 ? 'resolved' : 'processing'}</span>
      </div>

      <div className="investigation-grid">
        <div className="transaction">
          <span className="micro-label">BANK TRANSACTION</span>
          <strong>₹48,520.00</strong>
          <span>14 Aug 2026 · Settlement</span>
          <div className="transaction-tag">{stages[stage]}</div>
        </div>

        <div className="lineage">
          <div className="node">
            <span className="node-icon">
              <FileSearch size={16} />
            </span>
            <div>
              <b>Transaction received</b>
              <small>Reference: STLM-2848</small>
            </div>
          </div>

          <div className={`line ${stage > 0 ? 'active' : ''}`} />

          <div className="node">
            <span className="node-icon mint">
              <Sparkles size={16} />
            </span>
            <div>
              <b>Proposer</b>
              <small>{stage < 1 ? 'Waiting for ambiguity' : '3 candidates · fee check'}</small>
            </div>
          </div>

          <div className={`line ${stage > 1 ? 'active' : ''}`} />

          <div className="node">
            <span className="node-icon amber">
              <ShieldCheck size={16} />
            </span>
            <div>
              <b>Verifier</b>
              <small>
                {stage < 2
                  ? 'Adversarial review'
                  : stage === 2
                    ? 'Objection raised'
                    : 'Evidence accepted'}
              </small>
            </div>
          </div>
        </div>

        <div className="evidence">
          <div className="evidence-head">
            <span className="micro-label">EVIDENCE BUNDLE</span>
            <span>confidence {stage === 4 ? '99.8' : '72.4'}%</span>
          </div>

          <div className="candidate">
            <span>ledger_10428</span>
            <b>₹48,100.00</b>
            <Check size={15} />
          </div>

          <div className="candidate">
            <span>ledger_10431 + fee</span>
            <b>₹48,520.00</b>
            <Check size={15} />
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={stages[stage]}
              className={`verdict ${stage === 2 ? 'warning' : stage === 4 ? 'success' : ''}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: reduced ? 0 : 0.3 }}
            >
              <span>
                {stage === 2
                  ? 'Verifier found a gap'
                  : stage === 4
                    ? 'Decision confirmed'
                    : stage > 2
                      ? 'Retry added evidence'
                      : 'No confident result yet'}
              </span>
              <ArrowUpRight size={14} />
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      <div className="stage-bar">
        {stages.map((item, index) => (
          <span key={item} className={index <= stage ? 'on' : ''}>
            <i>{String(index + 1).padStart(2, '0')}</i>
            {item}
          </span>
        ))}
      </div>
    </Glass>
  )
}

function Hero() {
  const sceneRef = useRef<HTMLElement>(null)
  const reduced = useReducedMotion()

  return (
    <section id="top" ref={sceneRef} className="hero">
      <div className="ambient-field" aria-hidden="true">
        <span className="flow-ribbon ribbon-one" />
        <span className="flow-ribbon ribbon-two" />
        <span className="contour contour-one" />
        <span className="contour contour-two" />
        <span className="evidence-node node-a" />
        <span className="evidence-node node-b" />
      </div>

      <div className="hero-inner">
        <motion.div {...fade} className="hero-copy">
          <span className="eyebrow">THE RECONCILIATION LAYER FOR UNCERTAINTY</span>

          <h1>
            Reconciliation that
            <br />
            <em>knows when not to guess.</em>
          </h1>

          <p>
            Arbiter combines deterministic matching with adversarial reasoning, so
            your finance team gets fewer false positives and evidence for every decision.
          </p>

          <div className="hero-actions">
            <Link className="primary-btn" href="/runs">
              See a reconciliation run <ArrowUpRight size={16} />
            </Link>
            <span className="hero-note">Evidence before confidence.</span>
          </div>
        </motion.div>

        <motion.div
          className="hero-visual-wrap"
          initial={reduced ? false : { opacity: 0, y: 24, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: reduced ? 0 : 0.9, delay: 0.15, ease: [0.22, 1, 0.36, 1] }}
        >
          <InvestigationVisual mouseContainer={sceneRef} />
        </motion.div>
      </div>

      <div className="scroll-cue">
        <ArrowDown size={15} />
        scroll to investigate
      </div>
    </section>
  )
}

function Problem() {
  return (
    <section className="section problem">
      <motion.div {...fade} className="section-intro">
        <span className="eyebrow">THE FAILURE MODE</span>
        <h2>
          Most scripts are built
          <br />
          to be <em>certain.</em>
        </h2>
        <p>
          That works until the books stop behaving like a spreadsheet. A settlement arrives
          in parts. A reference is missing. A decoy looks almost right.
        </p>
      </motion.div>

      <motion.div {...fade} className="problem-art">
        <div className="receipt">
          <span>GATEWAY EXPORT</span>
          <strong>₹48,520</strong>
          <small>settlement_batch_08</small>
        </div>
        <div className="receipt offset">
          <span>INTERNAL LEDGER</span>
          <strong>₹24,260 × 2</strong>
          <small>two entries · one settlement</small>
        </div>
        <div className="artifact-note">
          <CircleAlert size={15} />
          one-to-one matching cannot explain this
        </div>
      </motion.div>
    </section>
  )
}

function Pipeline() {
  return (
    <section id="system" className="section pipeline">
      <motion.div {...fade} className="section-intro centered">
        <span className="eyebrow">THE TWO-TIER PIPELINE</span>
        <h2>
          Rules handle the obvious.
          <br />
          <em>Reasoning handles the rest.</em>
        </h2>
        <p>
          Fast-path matching keeps the common case efficient. Ambiguity gets a second
          system designed to challenge its own assumptions.
        </p>
      </motion.div>

      <div className="pipeline-flow">
        <div className="flow-card">
          <span className="flow-number">01</span>
          <h3>Fast path</h3>
          <p>
            Reference, amount, date, and deterministic tolerances resolve clean matches
            without unnecessary inference.
          </p>
          <div className="flow-result">
            <Check size={15} />
            deterministic first
          </div>
        </div>

        <div className="flow-connector" aria-hidden="true">→</div>

        <div className="flow-card featured">
          <span className="flow-number">02</span>
          <h3>Agent path</h3>
          <p>
            Ambiguous records are investigated with tools, evidence, a proposal, and an
            adversarial verification pass.
          </p>
          <div className="flow-result amber">
            <Sparkles size={15} />
            uncertainty is a result
          </div>
        </div>
      </div>
    </section>
  )
}

function Centerpiece() {
  return (
    <section className="section centerpiece">
      <motion.div {...fade} className="centerpiece-title">
        <span className="eyebrow">THE DIFFERENCE</span>
        <h2>
          A proposal is
          <br />
          <em>not a decision.</em>
        </h2>
      </motion.div>

      <motion.div {...fade} className="duel">
        <div className="actor proposer">
          <span className="actor-label">PROPOSER</span>
          <h3>“These two entries settle the amount.”</h3>
          <div className="quote-data">
            ledger_10431 + ledger_10432
            <br />
            <b>₹24,260 + ₹24,260 = ₹48,520</b>
          </div>
        </div>

        <div className="duel-axis">
          <span>challenge</span>
          <div><span /><span /></div>
          <b>↔</b>
        </div>

        <div className="actor verifier">
          <span className="actor-label">VERIFIER</span>
          <h3>“Show me the missing fee.”</h3>
          <div className="quote-data warning-text">
            <CircleAlert size={14} />
            description mismatch
            <br />
            <b>requesting additional evidence</b>
          </div>
        </div>
      </motion.div>

      <motion.div {...fade} className="decision-row">
        <div>
          <span className="decision-icon">01</span>
          <b>Agree</b>
          <small>confirm with evidence</small>
        </div>
        <div className="decision-arrow">→</div>
        <div className="decision-active">
          <span className="decision-icon">02</span>
          <b>Disagree</b>
          <small>retry with more evidence</small>
        </div>
        <div className="decision-arrow">→</div>
        <div>
          <span className="decision-icon">03</span>
          <b>Decide</b>
          <small>confirm or explain exception</small>
        </div>
      </motion.div>
    </section>
  )
}

function Metrics() {
  return (
    <section id="proof" className="section proof">
      <motion.div {...fade} className="section-intro">
        <span className="eyebrow">PROOF, NOT PROMISES</span>
        <h2>
          Every decision
          <br />
          leaves a <em>trail.</em>
        </h2>
        <p>
          Arbiter makes uncertainty visible. The goal is not to eliminate exceptions—it is
          to make every outcome defensible.
        </p>
      </motion.div>

      <div className="metric-grid">
        <div className="metric">
          <span>match rate</span>
          <strong>98.7<span>%</span></strong>
          <small>illustrative landing snapshot</small>
        </div>
        <div className="metric">
          <span>exceptions surfaced</span>
          <strong>143</strong>
          <small>illustrative landing snapshot</small>
        </div>
        <div className="metric">
          <span>false positives avoided</span>
          <strong>31<span>%</span></strong>
          <small>illustrative landing snapshot</small>
        </div>
      </div>
    </section>
  )
}

function Dataset() {
  return (
    <section className="section dataset">
      <motion.div {...fade} className="dataset-copy">
        <span className="eyebrow">THE ADVERSARIAL DATASET</span>
        <h2>
          Built to expose
          <br />
          <em>confident mistakes.</em>
        </h2>
        <p>
          Clean transactions are easy. Arbiter’s test set is deliberately uncomfortable:
          composite settlements, near misses, decoys, and anomalies that punish a guess.
        </p>
        <Link className="text-link" href="/runs">
          Explore the control room <ArrowUpRight size={15} />
        </Link>
      </motion.div>

      <motion.div {...fade} className="dataset-bars">
        {[
          ['clean 1:1', '62%', '62%'],
          ['many-to-one', '18%', '38%'],
          ['near misses + decoys', '12%', '26%'],
          ['anomalies', '8%', '18%'],
        ].map(([label, value, width]) => (
          <div className="dataset-bar-row" key={label}>
            <div className="bar-label">
              <span>{label}</span>
              <b>{value}</b>
            </div>
            <div className="bar">
              <i style={{ width }} />
            </div>
          </div>
        ))}
      </motion.div>
    </section>
  )
}

function Footer() {
  return (
    <footer>
      <div>
        <Link href="#top" className="brand">
          <span className="brand-mark">A</span>
          <span>arbiter</span>
        </Link>
        <p>
          Reconciliation for the records
          <br />
          that do not reconcile themselves.
        </p>
      </div>

      <div className="footer-cta">
        <span className="eyebrow">READY TO INVESTIGATE?</span>
        <Link href="/runs">
          Run a reconciliation <ArrowUpRight size={16} />
        </Link>
      </div>

      <span className="footer-meta">© 2026 Arbiter / built for honest books</span>
    </footer>
  )
}

export default function Page() {
  return (
    <main className="arbiter-site">
      <Nav />
      <Hero />
      <Problem />
      <Pipeline />
      <Centerpiece />
      <Metrics />
      <Dataset />
      <Footer />
    </main>
  )
}
