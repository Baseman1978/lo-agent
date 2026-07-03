declare module 'd3-force-3d' {
  // minimal shim for the forces this sandbox uses
  export function forceRadial(radius: number, x?: number, y?: number, z?: number): {
    strength(s: number): unknown;
  };
  export function forceY(y?: number): { strength(s: number): unknown };
}
