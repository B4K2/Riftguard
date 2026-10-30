import { useRef, type ReactNode } from 'react'

// Card with a soft white spotlight that follows the cursor.
export default function SpotlightCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const onMove = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect()
    ref.current!.style.setProperty('--x', `${e.clientX - r.left}px`)
    ref.current!.style.setProperty('--y', `${e.clientY - r.top}px`)
  }
  return (
    <div ref={ref} onMouseMove={onMove} className={`spotlight-card ${className}`}>
      {children}
    </div>
  )
}
