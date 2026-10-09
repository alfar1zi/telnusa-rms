/**
 * Language contract.
 *
 * Two languages, one flat key space per language. Keys are dotted and namespaced
 * by domain (`nav.dashboard`, `state.online`, `chart.scope.voltage.ac`), so a
 * slice file owns everything under its own prefix and two authors cannot collide.
 * `Dict` is deliberately a plain string map: it keeps lookup trivial and lets a
 * missing key fall back to English rather than rendering a raw key.
 */
export type Lang = "en" | "id";

export type Dict = Readonly<Record<string, string>>;

export const LANGS: ReadonlyArray<{ id: Lang; label: string }> = [
  { id: "en", label: "English" },
  { id: "id", label: "Bahasa Indonesia" },
];
