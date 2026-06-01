import { Component, type ReactNode } from "react";

interface Props {
  title: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class PhaseBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] p-4 text-sm">
          <p className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-error)">
            Couldn't render "{this.props.title}"
          </p>
          <p className="mt-1 text-(--color-ink-muted)">{this.state.error.message}</p>
        </div>
      );
    }
    return this.props.children;
  }
}
