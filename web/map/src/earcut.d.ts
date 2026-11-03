/* earcut ships no types; this declaration lets TypeScript import it. */
declare module "earcut" {
  export default function earcut(data: ArrayLike<number>, holeIndices?: ArrayLike<number>, dim?: number): number[];
}
