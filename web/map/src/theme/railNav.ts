/* Arrow-key travel along the map's Rail segmented control (the same rule as web/app/src/ui arrowTarget, kept here
 * because this package ships without the app): Left and Up go back, Right and Down forward, both wrap, Home and
 * End jump. Returns the index to focus or null for any other key. Pure. */
export function railArrow(key: string, from: number, count: number): number | null {
  if (count <= 0) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  const dir = key === "ArrowRight" || key === "ArrowDown" ? 1 : key === "ArrowLeft" || key === "ArrowUp" ? -1 : 0;
  if (!dir) return null;
  return (from + dir + count) % count;
}
