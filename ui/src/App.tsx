import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Bell, ChevronDown, FolderUp, Home, ChevronsLeft, ChevronsRight, Inbox as InboxIcon, LayoutGrid, Network } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { AlertsFeed } from '@/components/app/AlertsFeed'
import { Board } from '@/components/app/Board'
import { ClockBar, ClockControls } from '@/components/app/ClockBar'
import { DealDetail } from '@/components/app/DealDetail'
import { FraudGraph } from '@/components/app/FraudGraph'
import { Inbox } from '@/components/app/Inbox'
import { Overview } from '@/components/app/Overview'
import { UploadRun } from '@/components/app/UploadRun'
import { usePendingCount } from './state/pendingCount.ts'
import { buildHash } from './route.ts'
import type { Route } from './route.ts'
import { useClockStore } from './state/clockStore.ts'
import { useUiStore } from './state/uiStore.ts'

type NavItem = { id: Route['view']; label: string; route: Route; icon: LucideIcon }
// The rail lists only views that exist (D-11), grouped into sections.
const GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: 'Pipeline',
    items: [
      { id: 'overview', label: 'Overview', route: { view: 'overview' }, icon: Home },
      { id: 'pipeline', label: 'Board', route: { view: 'pipeline' }, icon: LayoutGrid },
      { id: 'inbox', label: 'Inbox', route: { view: 'inbox' }, icon: InboxIcon },
      { id: 'alerts', label: 'Alerts', route: { view: 'alerts' }, icon: Bell },
    ],
  },
  { title: 'Fraud', items: [{ id: 'graph', label: 'Graph', route: { view: 'graph', clusterId: null }, icon: Network }] },
  { title: 'Judge data', items: [{ id: 'upload', label: 'Upload & run', route: { view: 'upload' }, icon: FolderUp }] },
]

// Pipeline stays the active item while one of its deals is open.
const isActive = (item: Route['view'], current: Route['view']): boolean =>
  item === current || (item === 'pipeline' && current === 'deal')

function Section({ title, collapsed, children }: { title: string; collapsed: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(true)
  if (collapsed) return <div className="flex flex-col gap-1 border-t border-surface/15 pt-2">{children}</div>
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex items-center justify-between rounded-sm px-3 py-1 text-xs font-semibold tracking-wide text-surface/75 uppercase hover:text-surface"
      >
        {title}
        <ChevronDown className={`size-3.5 transition-transform ${open ? '' : '-rotate-90'}`} aria-hidden="true" />
      </button>
      {open && children}
    </div>
  )
}

function Rail() {
  const current = useUiStore((s) => s.route.view)
  const collapsed = useUiStore((s) => s.railCollapsed)
  const toggle = useUiStore((s) => s.toggleRail)
  const pending = usePendingCount()
  return (
    <aside aria-label="Primary" className={`rail-light flex min-h-screen shrink-0 flex-col gap-4 bg-navy pb-4 ${collapsed ? 'w-14' : 'w-56'}`}>
      <div className={`flex h-14 items-center border-b border-surface/15 ${collapsed ? 'justify-center' : 'justify-between px-3'}`}>
        {!collapsed && (
          <span className="flex items-center gap-2 text-base font-bold tracking-wide text-surface">
            <img src="/prism.svg" alt="" className="size-6" />
            Prism
          </span>
        )}
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          className="rounded-sm border border-surface/25 p-1 text-surface hover:bg-surface/10"
        >
          {collapsed ? <ChevronsRight className="size-4" aria-hidden="true" /> : <ChevronsLeft className="size-4" aria-hidden="true" />}
        </button>
      </div>
      <nav aria-label="Views" className={`flex flex-col gap-3 ${collapsed ? 'px-2' : 'px-2'}`}>
        {GROUPS.map((g) => (
          <Section key={g.title} title={g.title} collapsed={collapsed}>
            <ul className="flex flex-col gap-0.5">
              {g.items.map((item) => {
                const active = isActive(item.id, current)
                const Icon = item.icon
                return (
                  <li key={item.id}>
                    <a
                      href={buildHash(item.route)}
                      aria-current={active ? 'page' : undefined}
                      aria-label={collapsed ? item.label : undefined}
                      title={collapsed ? item.label : undefined}
                      className={`flex h-9 items-center gap-2 rounded-md text-sm transition-colors duration-150 focus-visible:outline-sky ${
                        collapsed ? 'justify-center' : 'px-3'
                      } ${active ? 'bg-surface font-semibold text-navy' : 'font-medium text-surface/80 hover:bg-surface/10 hover:text-surface'}`}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                      {!collapsed && <span className="flex-1">{item.label}</span>}
                      {!collapsed && item.id === 'inbox' && pending > 0 && (
                        <span className="rounded-sm bg-sky px-1.5 text-xs font-bold tabular-nums text-navy" aria-label={`${pending} pending`}>
                          {pending}
                        </span>
                      )}
                    </a>
                  </li>
                )
              })}
            </ul>
          </Section>
        ))}
        <Section title="Run" collapsed={collapsed}>
          <ClockControls collapsed={collapsed} />
        </Section>
      </nav>
    </aside>
  )
}

function Screen({ route }: { route: Route }) {
  switch (route.view) {
    case 'deal':
      return <DealDetail key={route.folder} folder={route.folder} />
    case 'inbox':
      return <Inbox />
    case 'alerts':
      return <AlertsFeed />
    case 'graph':
      return <FraudGraph clusterId={route.clusterId} />
    case 'overview':
      return <Overview />
    case 'upload':
      return <UploadRun />
    default:
      return <Board />
  }
}

export default function App() {
  const status = useClockStore((s) => s.status)
  const load = useClockStore((s) => s.load)
  const route = useUiStore((s) => s.route)

  // The only call site of the clock endpoint outside the smoke script (D-21).
  useEffect(() => {
    void load()
  }, [load])

  // The document title is the current page name (other views set their own).
  useEffect(() => {
    if (route.view === 'pipeline') document.title = 'Pipeline · Prism'
  }, [route])

  return (
    <div className="flex min-h-screen">
      <Rail />
      <div className="flex min-w-0 flex-1 flex-col">
        <ClockBar />
        <main className="flex min-w-0 flex-1 flex-col gap-4 p-6">{status === 'ready' && <Screen route={route} />}</main>
      </div>
    </div>
  )
}
