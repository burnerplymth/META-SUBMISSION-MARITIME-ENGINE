/**
 * The map's AIS status chip. The host passes the AIS feed's real state
 * from its source-health board, so the chip on the map, the pill in the
 * map bar and the status bar always say the same thing about the same
 * feed (never "AIS LIVE" beside "AIS - NO KEY").
 * Without a host state (the standalone page) the chip keeps its
 * "Awaiting key" text, which is the honest default there.
 */
export type AisFeedState = "LIVE" | "SNAPSHOT" | "DOWN" | "NOT SERVED" | "CHECKING";
export interface AisFeed {
  state: AisFeedState;
  /** The board says the feed is down for want of a key or config. */
  needsKey?: boolean;
  /** The host's reading of a DOWN feed that is simply not connected (never succeeded, nothing failing, or an
   *  offline copy): the chip is neutral, not an outage. Red stays for a feed that is configured and failing. */
  calm?: "not-served" | "offline";
}
export interface AisChipModel {
  lead: string;
  badge: string;
  /** Suffix of the `sm-mode-*` class. */
  cls: "live" | "snapshot" | "down" | "muted";
}

export function aisChipModel(feed: AisFeed | null | undefined): AisChipModel {
  if (!feed) return { lead: "AIS", badge: "Awaiting key", cls: "snapshot" };
  if (feed.state === "LIVE") return { lead: "AIS", badge: "LIVE", cls: "live" };
  if (feed.state === "SNAPSHOT") return { lead: "AIS", badge: "SNAPSHOT", cls: "snapshot" };
  if (feed.state === "CHECKING") return { lead: "AIS", badge: "CHECKING", cls: "snapshot" };
  if (feed.state === "DOWN" && feed.calm === "offline") return { lead: "AIS", badge: "Offline copy", cls: "muted" };
  if (feed.state === "DOWN" && feed.calm === "not-served") return { lead: "AIS", badge: "NOT SERVED", cls: "muted" };
  if (feed.state === "DOWN" && feed.needsKey) return { lead: "AIS", badge: "Awaiting key", cls: "snapshot" };
  if (feed.state === "NOT SERVED") return { lead: "AIS", badge: feed.state, cls: "muted" };
  return { lead: "AIS", badge: feed.state, cls: "down" };
}
