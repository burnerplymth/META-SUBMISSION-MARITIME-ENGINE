/**
 * SEARCH NAV BRIDGE — one-shot pending targets
 * between the search dispatcher and the panels that own the target
 * state, mirroring the mapFlyTo.ts register/consume pattern.
 *
 * Search can ask for a desk section, a saved exploration, a watchlist
 * or a replay event before the owning panel is mounted (its layout
 * switch is still in flight). The request is held as a pending
 * target; the owning panel registers a consumer on mount, and a
 * pending target is handed over the moment a consumer registers.
 * When a consumer is already registered, the request notifies it
 * immediately. Each target is consumed exactly once — consume*()
 * returns it and clears it — and a target that no longer exists when
 * consumed is the consuming panel's business to ignore quietly
 * (no error theatre anywhere in this pattern).
 *
 * Nothing here holds React state and nothing renders: the bridge
 * only carries keys and ids, which stay out of every display string.
 */

type Consumer = () => void;

function makeChannel<T extends string>() {
  let pending: T | null = null;
  let consumer: Consumer | null = null;
  return {
    request(id: T): void {
      pending = id;
      consumer?.();
    },
    register(fn: Consumer | null): void {
      consumer = fn;
      if (fn && pending !== null) fn();
    },
    consume(): T | null {
      const held = pending;
      pending = null;
      return held;
    },
  };
}

const deskChannel = makeChannel<string>();
const explorationChannel = makeChannel<string>();
const watchlistChannel = makeChannel<string>();
const replayChannel = makeChannel<string>();
const toolChannel = makeChannel<string>();

/** Ask the Analysis Desks panel to scroll a desk section into view. */
export const requestDesk = (desk: string): void => deskChannel.request(desk);
export const registerDeskConsumer = (fn: Consumer | null): void => deskChannel.register(fn);
export const consumeDesk = (): string | null => deskChannel.consume();

/** Ask the ENS panel to switch to explorations and open one. */
export const requestExploration = (id: string): void => explorationChannel.request(id);
export const registerExplorationConsumer = (fn: Consumer | null): void => explorationChannel.register(fn);
export const consumeExploration = (): string | null => explorationChannel.consume();

/** Ask the ALR panel to select/highlight one watchlist. */
export const requestWatchlist = (id: string): void => watchlistChannel.request(id);
export const registerWatchlistConsumer = (fn: Consumer | null): void => watchlistChannel.register(fn);
export const consumeWatchlist = (): string | null => watchlistChannel.consume();

/** Ask the Analysis Desks panel to select one replay in REPLAY. */
export const requestReplay = (eventId: string): void => replayChannel.request(eventId);
export const registerReplayConsumer = (fn: Consumer | null): void => replayChannel.register(fn);
export const consumeReplay = (): string | null => replayChannel.consume();

/** Ask the shell to open a rail tool (HIS, RLS, SCN, PRT, EXP) in the tool
 *  slot. App owns the slot, so App registers the consumer. */
export const requestTool = (tool: string): void => toolChannel.request(tool);
export const registerToolConsumer = (fn: Consumer | null): void => toolChannel.register(fn);
export const consumeTool = (): string | null => toolChannel.consume();

