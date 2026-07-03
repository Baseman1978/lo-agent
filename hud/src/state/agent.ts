// Mock agent state machine: in production this comes from the Fastify WebSocket
// (Claude Agent SDK run events); here it cycles automatically so the orb
// behaviour can be judged without a backend. Manual override via the UI.

export type AgentState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'alert';

export const AGENT_STATES: AgentState[] = ['idle', 'listening', 'thinking', 'speaking', 'alert'];

export const STATE_COLOR: Record<AgentState, number> = {
  idle: 0x67e8f9, // cyan
  listening: 0x34d399, // emerald
  thinking: 0xa78bfa, // violet
  speaking: 0xfbbf24, // amber
  alert: 0xf87171, // rood — waarschuwing (productie: HITL wacht / veiligheidsevent)
};

export const STATE_LABEL: Record<AgentState, string> = {
  idle: 'IDLE',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
  alert: 'WARNING',
};

const AUTO_SEQUENCE: [AgentState, number][] = [
  ['idle', 6000],
  ['listening', 4500],
  ['thinking', 4000],
  ['speaking', 6000],
];

export class AgentStateMock {
  state: AgentState = 'idle';
  auto = true;
  private seqIndex = 0;
  private nextAt = performance.now() + AUTO_SEQUENCE[0]![1];
  private listeners: ((s: AgentState) => void)[] = [];

  onChange(fn: (s: AgentState) => void): void {
    this.listeners.push(fn);
  }

  set(state: AgentState, manual = false): void {
    if (manual) this.auto = false;
    if (state === this.state) return;
    this.state = state;
    for (const fn of this.listeners) fn(state);
  }

  setAuto(on: boolean): void {
    this.auto = on;
    if (on) this.nextAt = performance.now() + 1500;
  }

  update(now: number): void {
    if (!this.auto || now < this.nextAt) return;
    this.seqIndex = (this.seqIndex + 1) % AUTO_SEQUENCE.length;
    const [state, dur] = AUTO_SEQUENCE[this.seqIndex]!;
    this.nextAt = now + dur;
    this.set(state);
  }
}
