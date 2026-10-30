import { useEffect, useRef, useState } from 'react'

const GLYPHS = '!<>-_\\/[]{}=+*^?#01'

// Scrambles through random glyphs, settling left to right once the element is visible.
export default function DecryptedText({ text, className, speed = 28 }: { text: string; className?: string; speed?: number }) {
  const [out, setOut] = useState(text.replace(/\S/g, ' '))
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    let timer: number | undefined
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return
      io.disconnect()
      let frame = 0
      timer = window.setInterval(() => {
        frame++
        const settled = Math.floor(frame / 2)
        setOut([...text].map((c, i) => (i < settled || c === ' ' ? c : GLYPHS[(Math.random() * GLYPHS.length) | 0])).join(''))
        if (settled >= text.length) window.clearInterval(timer)
      }, speed)
    }, { threshold: 0.6 })
    io.observe(ref.current!)
    return () => { io.disconnect(); window.clearInterval(timer) }
  }, [text, speed])
  return <span ref={ref} className={className} aria-label={text}>{out}</span>
}
