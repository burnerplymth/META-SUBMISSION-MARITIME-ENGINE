/**
 * Monotonic request tickets, the store's existing last-issued-
 * wins pattern (moneyReq / impactRunReq / simStateReq) factored into
 * one box type so every guarded slice uses identical mechanics:
 * issuing a ticket invalidates every earlier ticket; a response whose
 * ticket is no longer current is dropped, never applied; bumping the
 * box without issuing (e.g. exitSim) invalidates everything in
 * flight.
 */
export interface TicketBox { current: number }

export const newTicketBox = (): TicketBox => ({ current: 0 });

/** Issue the next ticket; it becomes the only current one. */
export function issueTicket(box: TicketBox): number {
  box.current += 1;
  return box.current;
}

/** True iff `ticket` is the most recently issued one on this box. */
export function isCurrentTicket(box: TicketBox, ticket: number): boolean {
  return box.current === ticket;
}

/** Invalidate every outstanding ticket without issuing a new one. */
export function bumpTicket(box: TicketBox): void {
  box.current += 1;
}
