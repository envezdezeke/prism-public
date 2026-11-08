import type { ReactNode } from 'react'
import { CircleAlert } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DASH, NULL_TOOLTIP } from '../../lib/format.ts'
import { buildHash } from '../../route.ts'

// Navy text link (02-UI-SPEC accent item 6). Focus ring comes from the base layer.
export const LINK_CLASS = 'text-navy underline-offset-2 hover:underline'

// A same-app hash route. The href is only ever "#/deals/" plus an encodeURIComponent-ed folder, never a
// URL taken from engine data. The hashchange listener in uiStore moves the route, and Back works.
export function DealLink({ folder, className, children }: { folder: string; className?: string; children: ReactNode }) {
  return (
    <a href={buildHash({ view: 'deal', folder })} className={className ?? LINK_CLASS}>
      {children}
    </a>
  )
}

export function PipelineLink({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <a href={buildHash({ view: 'pipeline' })} className={className ?? LINK_CLASS}>
      {children}
    </a>
  )
}

// The only place the display serif and the page-title size are applied.
export function PageTitle({ children }: { children: ReactNode }) {
  return <h1 className="font-display text-title font-semibold tracking-tight text-ink">{children}</h1>
}

export function Caption({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={`text-xs text-ink-2${className ? ` ${className}` : ''}`}>{children}</p>
}

// A missing value: the em dash in the tertiary token, with the standard tooltip.
export function NullValue({ title = NULL_TOOLTIP }: { title?: string }) {
  return (
    <span className="text-tertiary" title={title}>
      {DASH}
    </span>
  )
}

export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="text-sm text-ink-2">
      {children}
    </p>
  )
}

export function EmptyState({ heading, children }: { heading?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-line bg-surface p-4">
      {heading && <p className="text-base font-bold text-navy">{heading}</p>}
      {children && <div className="text-sm text-ink-2">{children}</div>}
    </div>
  )
}

// Neutral on purpose: red is reserved for a fraud hold (D-05, D-20).
export function ErrorState({
  heading,
  message,
  onRetry,
}: {
  heading?: string
  message: string
  onRetry?: () => void
}) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-line bg-surface p-4 text-ink">
      <CircleAlert className="mt-0.5 size-4 shrink-0 text-ink-2" aria-hidden="true" />
      <div className="flex flex-col gap-2">
        {heading && <p className="text-base font-bold text-navy">{heading}</p>}
        <p className="text-sm">{message}</p>
        {onRetry && (
          <div>
            <Button variant="outline" size="sm" onClick={onRetry}>
              Retry
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

// Page header used by every screen: title, an uppercase breadcrumb subtitle, then a toolbar row.
export function PageHeader({
  title,
  crumbs,
  toolbar,
  children,
}: {
  title: ReactNode
  crumbs: string[]
  toolbar?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <PageTitle>{title}</PageTitle>
        <p className="pl-4 text-xs font-semibold tracking-wide text-ink-2 uppercase">{['Prism', ...crumbs].join(' / ')}</p>
      </div>
      {children}
      {toolbar && <div className="flex flex-wrap items-center gap-2 border-y border-line py-2">{toolbar}</div>}
    </header>
  )
}

// Empty state with an outline icon, a headline, one sentence and at most one action.
export function IconEmptyState({
  icon: Icon,
  heading,
  children,
  action,
}: {
  icon: LucideIcon
  heading: string
  children?: ReactNode
  action?: { label: string; onClick: () => void }
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-line bg-surface px-6 py-10 text-center">
      <Icon className="size-8 text-ink-2" strokeWidth={1.5} aria-hidden="true" />
      <p className="text-base font-semibold text-navy">{heading}</p>
      {children && <div className="max-w-md text-sm text-ink-2">{children}</div>}
      {action && (
        <Button size="sm" className="mt-2" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  )
}
