'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import dynamic from 'next/dynamic'
import {
  ArrowDown,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleAlert,
  FileSearch,
  Menu,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import { Brand } from '@/components/brand'

const LiquidGlass = dynamic(() => import('liquid-glass-react'), {
  ssr: false,
})

const fade = {
  initial: { opacity: 0, y: 18 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, amount: 0.22 },
  transition: {
    duration: 0.7,
    ease: 'easeOut' as const,
  },
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
  const [open, setOpen] = useState(false)

  return (
    <>
      <header className="nav">
        <span onClick={() => setOpen(false)}>
          <Brand href="#top" />
        </span>

        <nav className="nav-links">
          <Link href="#system">How it works</Link>
          <Link href="#proof">Proof</Link>
          <Link href="/runs">
            Open control room <ArrowUpRight size={14} />
          </Link>
        </nav>

        <Link className="nav-cta" href="/runs">
          Run a reconciliation <ChevronRight size={15} />
        </Link>

        <button
          className="menu"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </header>

      <AnimatePresence>
        {open && (
          <motion.div
            className="mobile-menu"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
          >
            <Link href="#system" onClick={() => setOpen(false)}>
              How it works
            </Link>

            <Link href="#proof" onClick={() => setOpen(false)}>
              Proof
            </Link>

            <Link href="/runs" onClick={() => setOpen(false)}>
              Open control room <ArrowUpRight size={14} />
            </Link>

            <Link
              href="/runs"
              className="mobile-menu-cta"
              onClick={() => setOpen(false)}
            >
              Run a reconciliation <ArrowUpRight size={14} />
            </Link>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function InvestigationVisual({
  mouseContainer,
}: {
  mouseContainer: React.RefObject<HTMLElement | null>
}) {
  const [stage, setStage] = useState(0)

  useEffect(() => {
    const timers = [700, 1500, 2600, 3900].map((delay, index) =>
      window.setTimeout(() => setStage(index + 1), delay),
    )

    return () => timers.forEach(window.clearTimeout)
  }, [])

  const stages = [
    'AMBIGUOUS',
    'PROPOSER',
    'VERIFIER',
    'RETRY',
    'CONFIRMED',
  ]

  return (
    <Glass
      liquid
      mouseContainer={mouseContainer}
      className="investigation"
    >
      <div className="visual-top">
        <span className="eyebrow">LIVE INVESTIGATION / 00428</span>
        <span className="status-dot">● processing</span>
      </div>

      <div className="investigation-grid">
        <div className="transaction">
          <span className="micro-label">BANK TRANSACTION</span>

          <strong>₹48,520.00</strong>

          <span>14 Aug 2026 · Settlement</span>

          <div className="transaction-tag">
            {stages[stage]}
          </div>
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

          <div
            className={`line ${stage > 0 ? 'active' : ''}`}
          />

          <div className="node">
            <span className="node-icon mint">
              <Sparkles size={16} />
            </span>

            <div>
              <b>Proposer</b>

              <small>
                {stage < 1
                  ? 'Waiting for ambiguity'
                  : '3 candidates · fee check'}
              </small>
            </div>
          </div>

          <div
            className={`line ${stage > 1 ? 'active' : ''}`}
          />

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

            <span>
              confidence {stage === 4 ? '99.8' : '72.4'}%
            </span>
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

          <div
            className={`verdict ${
              stage === 2
                ? 'warning'
                : stage === 4
                  ? 'success'
                  : ''
            }`}
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
          </div>
        </div>
      </div>

      <div className="stage-bar">
        {stages.map((stageName, index) => (
          <span
            key={stageName}
            className={index <= stage ? 'on' : ''}
          >
            <i>{String(index + 1).padStart(2, '0')}</i>
            {stageName}
          </span>
        ))}
      </div>
    </Glass>
  )
}

function Hero() {
  const sceneRef = useRef<HTMLElement>(null)

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

      <motion.div {...fade} className="hero-copy">
        <span className="eyebrow">
          THE RECONCILIATION LAYER FOR UNCERTAINTY
        </span>

        <h1>
          Reconciliation that
          <br />
          <em>knows when not to guess.</em>
        </h1>

        <p>
          Arbiter combines deterministic matching with adversarial reasoning,
          so your finance team gets fewer false positives—and evidence for
          every decision.
        </p>

        <Link className="primary-btn" href="/runs">
          See a reconciliation run <ArrowUpRight size={16} />
        </Link>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 1, delay: 0.2 }}
      >
        <InvestigationVisual mouseContainer={sceneRef} />
      </motion.div>

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
          That works until the books stop behaving like a spreadsheet. A
          settlement arrives in parts. A reference is missing. A decoy looks
          almost right.
        </p>
      </motion.div>

      <motion.div {...fade} className="problem-art">
        <div className="problem-card gateway-card">
          <span>GATEWAY EXPORT</span>
          <strong>₹48,520</strong>
          <small>settlement_batch_08</small>
        </div>

        

        <div className="problem-card ledger-card">
          <span>INTERNAL LEDGER</span>
          <strong></strong>
          <small>two entries · one settlement</small>
        </div>

        <div className="artifact-note">
          <CircleAlert size={15} />
          <span>one-to-one matching cannot explain this</span>
        </div>
      </motion.div>
    </section>
  )
}

function Pipeline() {
  return (
    <section id="system" className="section pipeline">
      <motion.div
        {...fade}
        className="section-intro centered"
      >
        <span className="eyebrow">THE TWO-TIER PIPELINE</span>

        <h2>
          Rules handle the obvious.
          <br />
          <em>Reasoning handles the rest.</em>
        </h2>

        <p>
          Fast-path matching keeps the common case efficient. Ambiguity gets a
          second system designed to challenge its own assumptions.
        </p>
      </motion.div>

      <div className="pipeline-flow">
        <div className="flow-card">
          <span className="flow-number">01</span>

          <h3>Fast path</h3>

          <p>
            Reference, amount, date, and deterministic tolerances resolve
            clean matches without unnecessary inference.
          </p>

          <div className="flow-result">
            <Check size={15} /> 92.4% confirmed
          </div>
        </div>

        <div className="flow-connector">→</div>

        <div className="flow-card featured">
          <span className="flow-number">02</span>

          <h3>Agent path</h3>

          <p>
            Ambiguous records are investigated with tools, evidence, a
            proposal, and an adversarial verification pass.
          </p>

          <div className="flow-result amber">
            <Sparkles size={15} /> uncertainty is a result
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

          <h3>
            “These two entries
            <br />
            settle the amount.”
          </h3>

          <div className="quote-data">
            ledger_10431 + ledger_10432
            <br />
            <b>₹24,260 + ₹24,260 = ₹48,520</b>
          </div>
        </div>

        <div className="duel-axis">
          <span>challenge</span>

          <div>
            <span />
            <span />
          </div>

          <b>↔</b>
        </div>

        <div className="actor verifier">
          <span className="actor-label">VERIFIER</span>

          <h3>
            “Show me the
            <br />
            missing fee.”
          </h3>

          <div className="quote-data warning-text">
            <CircleAlert size={14} /> description mismatch
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
          Arbiter makes uncertainty visible. The goal is not to eliminate
          exceptions—it is to make every outcome defensible.
        </p>
      </motion.div>

      <div className="metric-grid">
        <div className="metric">
          <span>match rate</span>

          <strong>
            98.7<span>%</span>
          </strong>

          <small>full pipeline / 10k records</small>
        </div>

        <div className="metric">
          <span>exceptions surfaced</span>

          <strong>143</strong>

          <small>explained, not silently dropped</small>
        </div>

        <div className="metric">
          <span>false positives avoided</span>

          <strong>
            31<span>%</span>
          </strong>

          <small>vs deterministic baseline</small>
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
          Clean transactions are easy. Arbiter’s test set is deliberately
          uncomfortable: composite settlements, near misses, decoys, and
          anomalies that punish a guess.
        </p>

        <Link className="text-link" href="/runs">
          Explore the control room <ArrowUpRight size={15} />
        </Link>
      </motion.div>

      <motion.div {...fade} className="dataset-bars">
        <div className="bar-label">
          <span>clean 1:1</span>
          <b>62%</b>
        </div>

        <div className="bar">
          <i style={{ width: '62%' }} />
        </div>

        <div className="bar-label">
          <span>many-to-one</span>
          <b>18%</b>
        </div>

        <div className="bar">
          <i
            style={{ width: '38%' }}
            className="mint-fill"
          />
        </div>

        <div className="bar-label">
          <span>near misses + decoys</span>
          <b>12%</b>
        </div>

        <div className="bar">
          <i
            style={{ width: '26%' }}
            className="amber-fill"
          />
        </div>

        <div className="bar-label">
          <span>anomalies</span>
          <b>8%</b>
        </div>

        <div className="bar">
          <i
            style={{ width: '18%' }}
            className="rose-fill"
          />
        </div>
      </motion.div>
    </section>
  )
}

function Footer() {
  return (
    <footer>
      <div>
        <Brand href="#top" />

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

      <span className="footer-meta">
        © 2026 Arbiter / built for honest books
      </span>
    </footer>
  )
}

export default function Page() {
  return (
    <main>
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