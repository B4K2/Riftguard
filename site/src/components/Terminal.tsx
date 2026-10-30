import { useLayoutEffect, useRef } from 'react'
import { gsap } from 'gsap'

export type Line = { text: string; kind?: 'cmd' | 'dim' | 'warn' | 'plain' }

// Terminal whose lines type in one after another as it scrolls into view.
export default function Terminal({ lines, title = 'zsh' }: { lines: Line[]; title?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ scrollTrigger: { trigger: ref.current, start: 'top 75%' } })
      ref.current!.querySelectorAll<HTMLElement>('.term-line').forEach((el) => {
        const full = el.dataset.text!
        const o = { n: 0 }
        el.textContent = ''
        tl.to(o, {
          n: full.length,
          duration: Math.min(full.length * 0.012, 0.9),
          ease: 'none',
          onUpdate: () => { el.textContent = full.slice(0, Math.round(o.n)) },
        })
      })
    }, ref)
    return () => ctx.revert()
  }, [lines])
  return (
    <div ref={ref} className="terminal">
      <div className="term-bar"><i /><i /><i /><span>{title}</span></div>
      <pre>
        {lines.map((l, i) => (
          <div key={i} className={`term-line ${l.kind ?? 'plain'}`} data-text={l.text}>{l.text}</div>
        ))}
      </pre>
    </div>
  )
}
