/* The lane-geometry chip of the map's status strip.
 * NOT SERVED when the lane feed failed (the map then draws the chords it always had, and says so);
 * Approx. <n> when lanes are in hand and n sea legs have none; nothing otherwise. */
export interface LaneChip { kind: "not_served" | "approx"; label: string; value: string }

export function laneChipModel(s: { notServed: boolean; hasLanes: boolean; approxLegs: number }): LaneChip | null {
  if (s.notServed) return { kind: "not_served", label: "Lane geometry", value: "NOT SERVED" };
  if (s.hasLanes && s.approxLegs > 0) return { kind: "approx", label: "Approx.", value: s.approxLegs.toLocaleString("en-US") };
  return null;
}
