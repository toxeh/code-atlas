export function renderDashboard(user: string): string {
  return `hello ${user}`;
}

export class Widget {
  constructor(private readonly id: string) {}

  mount(): void {
    // no-op
  }
}

export const boot = async () => {
  return renderDashboard("anon");
};
