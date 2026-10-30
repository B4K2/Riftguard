import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import Lenis from 'lenis'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import Scene, { scroll } from './components/Scene'
import SplitText from './components/SplitText'
import DecryptedText from './components/DecryptedText'
import SpotlightCard from './components/SpotlightCard'
import Terminal from './components/Terminal'

gsap.registerPlugin(ScrollTrigger)

const REPO = 'https://github.com/B4K2/Riftguard'

const FEATURES = [
  { k: '01', t: 'Shared decisions', d: 'Agents call check_decisions before architectural choices and record_decision after. Every worktree sees the same decisions instantly.' },
  { k: '02', t: 'Conflict judge', d: 'A fresh, tool-less claude -p or codex exec session decides whether two decisions really clash. Postgres + Redis is fine. Postgres vs MongoDB is not.' },
  { k: '03', t: 'Trajectory recording', d: 'Hooks record every tool call into a content-addressed store, each step linked to its parent like a git commit. Zero tokens spent.' },
  { k: '04', t: 'Automatic checkpoints', d: 'Every call is tagged pure, reversible or irreversible, and a checkpoint is saved just before anything that can’t be undone.' },
  { k: '05', t: 'Rewind & fork', d: 'Move an agent’s history back to a checkpoint, or start a new agent from one, seeded with a summary instead of the full transcript.' },
  { k: '06', t: 'Zero secrets', d: 'The judge runs on the agent CLI you’re already logged into. No API keys, no server, it sits beside git instead of replacing it.' },
]

const STEPS = [
  { t: 'Hooks record', d: 'PreToolUse / PostToolUse hooks capture each tool call silently. Agents never see them, so recording is free.' },
  { t: 'One store per repo', d: 'Every git worktree resolves to the main repo’s SQLite store. A decision in one worktree is visible in all of them, before any merge.' },
  { t: 'MCP tools', d: 'check_decisions, record_decision, get_decision and checkpoint. Replies stay compact: only matching decisions are shown in full.' },
  { t: 'Independent judge', d: 'When topics overlap, a judge that never saw the requesting agent’s conversation rules on the clash. If no CLI is around, a heuristic takes over.' },
]

const COMMANDS = ['riftguard init', 'riftguard decisions', 'riftguard conflicts', 'riftguard resolve 4 --keep 3', 'riftguard audit', 'riftguard status', 'riftguard log', 'riftguard checkpoint', 'riftguard rewind', 'riftguard fork']

function CopyCmd({ cmd }: { cmd: string }) {
  const [done, setDone] = useState(false)
  return (
    <button className="copy" onClick={() => { navigator.clipboard.writeText(cmd); setDone(true); setTimeout(() => setDone(false), 1400) }}>
      <span className="prompt">$</span> {cmd}
      <span className="copy-tag">{done ? 'copied' : 'copy'}</span>
    </button>
  )
}

export default function App() {
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const lenis = new Lenis({ lerp: 0.09 })
    lenis.on('scroll', (e: Lenis) => { scroll.progress = e.progress; ScrollTrigger.update() })
    const raf = (t: number) => lenis.raf(t * 1000)
    gsap.ticker.add(raf)
    gsap.ticker.lagSmoothing(0)
    return () => { gsap.ticker.remove(raf); lenis.destroy() }
  }, [])

  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.hero .fade', { y: 24, opacity: 0, duration: 1, stagger: 0.12, delay: 0.5, ease: 'power3.out' })

      gsap.utils.toArray<HTMLElement>('.reveal').forEach((el) =>
        gsap.from(el, { y: 60, opacity: 0, duration: 1, ease: 'power3.out', scrollTrigger: { trigger: el, start: 'top 85%' } }),
      )

      // The two agents split apart as the problem section scrolls by.
      gsap.timeline({ scrollTrigger: { trigger: '.problem', start: 'top 70%', end: 'center 40%', scrub: 1 } })
        .fromTo('.agent-a', { x: 0 }, { x: -40 }, 0)
        .fromTo('.agent-b', { x: 0 }, { x: 40 }, 0)
        .fromTo('.rift-line', { scaleY: 0 }, { scaleY: 1 }, 0)

      // Pinned "how it works": the progress bar fills and each step lights up in turn.
      const steps = gsap.utils.toArray<HTMLElement>('.step')
      ScrollTrigger.create({
        trigger: '.how',
        start: 'top top',
        end: `+=${steps.length * 70}%`,
        pin: true,
        scrub: true,
        onUpdate: (self) => {
          const active = Math.min(steps.length - 1, Math.floor(self.progress * steps.length))
          steps.forEach((s, i) => s.classList.toggle('active', i === active))
          gsap.set('.how-bar i', { scaleY: self.progress })
        },
      })

      gsap.from('.feature', { y: 80, opacity: 0, stagger: 0.08, duration: 0.9, ease: 'power3.out', scrollTrigger: { trigger: '.features-grid', start: 'top 80%' } })

      gsap.to('.marquee-track', { xPercent: -50, ease: 'none', scrollTrigger: { trigger: '.marquee', start: 'top bottom', end: 'bottom top', scrub: 1 } })
    }, root)
    return () => ctx.revert()
  }, [])

  return (
    <div ref={root}>
      <Scene />
      <div className="grain" />

      <nav className="nav">
        <a href="#top" className="logo"><span className="logo-mark" />riftguard</a>
        <div className="nav-links">
          <a href="#problem">Problem</a>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href={REPO} target="_blank" rel="noreferrer" className="btn-ghost">GitHub ↗</a>
        </div>
      </nav>

      <header className="hero" id="top">
        <div className="hero-inner">
          <p className="eyebrow fade">v0.2.0 · Claude Code & Codex</p>
          <SplitText as="h1" text="Keep parallel agents from silently contradicting each other." className="hero-title" delay={0.2} />
          <p className="lede fade">
            Riftguard gives your coding agents a shared memory of architectural decisions and an independent judge,
            so the second agent is told <em>before</em> it writes any code.
          </p>
          <div className="hero-cta fade">
            <CopyCmd cmd="uv tool install riftguard" />
            <a className="btn" href={REPO} target="_blank" rel="noreferrer">View on GitHub</a>
          </div>
        </div>
        <div className="scroll-hint fade"><span />scroll</div>
      </header>

      <section className="problem" id="problem">
        <p className="eyebrow reveal">The problem</p>
        <SplitText text="The conflict is in the ideas, not the lines." className="section-title" by="words" />
        <div className="agents">
          <div className="agent-card agent-a reveal">
            <span className="tag">agent A · worktree 1</span>
            <strong>Postgres</strong>
            <p>“relational data, need transactions”</p>
          </div>
          <div className="rift-line" />
          <div className="agent-card agent-b reveal">
            <span className="tag">agent B · worktree 2</span>
            <strong>MongoDB</strong>
            <p>“flexible schema”</p>
          </div>
        </div>
        <p className="body reveal">
          The code merges cleanly. Git sees nothing wrong. You find out the system is incoherent at review time.
          Riftguard catches it the moment agent B tries to record its choice.
        </p>
        <div className="reveal">
          <Terminal
            title="agent B"
            lines={[
              { text: '▸ record_decision("datastore", "MongoDB", "flexible schema")', kind: 'cmd' },
              { text: '' },
              { text: 'HELD FOR REVIEW: #4 [datastore] MongoDB conflicts with #3 [database] Postgres', kind: 'warn' },
              { text: 'by agent-a (severity high, confidence 0.95, judge: claude).', kind: 'warn' },
              { text: 'Judge: Keep Postgres (#3); transactions are a hard requirement,', kind: 'plain' },
              { text: 'and JSONB covers flexible schemas. Do not build on MongoDB.', kind: 'plain' },
              { text: '' },
              { text: '$ riftguard resolve 4 --keep 3', kind: 'cmd' },
              { text: '✓ kept #3, rejected #4', kind: 'dim' },
            ]}
          />
        </div>
      </section>

      <section className="how" id="how">
        <div className="how-left">
          <p className="eyebrow">How it works</p>
          <h2 className="section-title"><DecryptedText text="Sits beside git." /><br /><span className="muted">Not instead of it.</span></h2>
          <div className="how-bar"><i /></div>
        </div>
        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.t} className={`step ${i === 0 ? 'active' : ''}`}>
              <span className="step-n">0{i + 1}</span>
              <div><h3>{s.t}</h3><p>{s.d}</p></div>
            </li>
          ))}
        </ol>
      </section>

      <section className="features" id="features">
        <p className="eyebrow reveal">Features</p>
        <SplitText text="Everything an agent fleet needs to agree." className="section-title" by="words" />
        <div className="features-grid">
          {FEATURES.map((f) => (
            <SpotlightCard key={f.k} className="feature">
              <span className="feature-k">{f.k}</span>
              <h3>{f.t}</h3>
              <p>{f.d}</p>
            </SpotlightCard>
          ))}
        </div>
      </section>

      <div className="marquee" aria-hidden>
        <div className="marquee-track">
          {[...COMMANDS, ...COMMANDS].map((c, i) => <span key={i}>{c}<b>/</b></span>)}
        </div>
      </div>

      <section className="start" id="start">
        <p className="eyebrow reveal">Quick start</p>
        <SplitText text="Three commands. No API keys." className="section-title" by="words" />
        <div className="reveal">
          <Terminal
            title="your-repo"
            lines={[
              { text: '$ uv tool install riftguard', kind: 'cmd' },
              { text: '$ cd your-repo && riftguard init', kind: 'cmd' },
              { text: '  wired up Claude Code (hooks, .mcp.json, CLAUDE.md)', kind: 'dim' },
              { text: '  wired up Codex (.codex/config.toml, AGENTS.md)', kind: 'dim' },
              { text: '$ riftguard conflicts', kind: 'cmd' },
              { text: '  no decisions held for review', kind: 'dim' },
            ]}
          />
        </div>
        <div className="final-cta reveal">
          <CopyCmd cmd="pipx install riftguard" />
          <a className="btn" href={REPO} target="_blank" rel="noreferrer">Star on GitHub</a>
        </div>
      </section>

      <footer className="footer">
        <span>riftguard · MIT License</span>
        <span className="muted">Checkpoints adapted from AgentGit (Li et al., 2025)</span>
      </footer>
    </div>
  )
}
