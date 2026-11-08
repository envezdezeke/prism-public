import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { ErrorState } from './Primitives.tsx'

interface Props {
  // Changing this (for example the route) clears a caught error, so leaving a broken screen recovers it.
  resetKey: string
  children: ReactNode
}

interface State {
  failed: boolean
  key: string
}

// One value the screen cannot read must not blank the whole app (WR-07). Neutral on purpose, like every
// error state: red is reserved for a fraud hold. The message never shows the error text or a stack.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, key: this.props.resetKey }

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Nothing to report in Phase 2; the boundary only keeps the rest of the app alive.
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children
    return (
      <ErrorState
        heading="Could not display this screen."
        message="A value in the data could not be read. The clock and the other screens still work."
        onRetry={() => this.setState({ failed: false })}
      />
    )
  }
}
