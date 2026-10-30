import { useLayoutEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

type Props = { text: string; className?: string; as?: 'h1' | 'h2' | 'p'; delay?: number; by?: 'chars' | 'words' }

// Per-character (or per-word) rise-in, triggered when the element scrolls into view.
export default function SplitText({ text, className, as: Tag = 'h2', delay = 0, by = 'chars' }: Props) {
  const ref = useRef<HTMLHeadingElement>(null)
  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(ref.current!.querySelectorAll('.st-unit'), {
        yPercent: 110,
        opacity: 0,
        rotate: 4,
        duration: 0.9,
        ease: 'power4.out',
        stagger: by === 'chars' ? 0.018 : 0.05,
        delay,
        scrollTrigger: { trigger: ref.current, start: 'top 85%' },
      })
    }, ref)
    return () => ctx.revert()
  }, [delay, by])

  const words = text.split(' ')
  return (
    <Tag ref={ref} className={className} aria-label={text}>
      {words.map((w, wi) => (
        <span key={wi} className="st-word" aria-hidden>
          {by === 'chars'
            ? [...w].map((c, ci) => <span key={ci} className="st-unit">{c}</span>)
            : <span className="st-unit">{w}</span>}
          {wi < words.length - 1 && ' '}
        </span>
      ))}
    </Tag>
  )
}
