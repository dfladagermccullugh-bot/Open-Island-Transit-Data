// Prune: free space in the collector, one archived day at a time.
//
// A day's stored responses are deleted only when all of these hold:
//   1. the day is listed in the archive manifest on GitHub,
//   2. every file the manifest lists for it can be downloaded at its recorded size,
//   3. the database's own row count and digest for the day equal the manifest's
//      (checked again inside the database by prune_raw),
//   4. the day is at least 3 days old.
// The fetch log is never deleted.

export interface ManifestFile {
  name: string;
  bytes: number;
  sha256: string;
}

export interface ManifestDay {
  service_date: string;
  raw_rows: number;
  digest: string;
  release_tag?: string;
  files: ManifestFile[];
  static?: { static_fetch_id: number; sha256: string | null; file?: string }[];
}

export interface Manifest {
  days: Record<string, ManifestDay>;
}

const DAY_MS = 86_400_000;

/** Calendar date (YYYY-MM-DD) in America/Los_Angeles for an instant. */
export function pacificDate(at: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(at);
  return parts; // en-CA formats as YYYY-MM-DD
}

function addDays(day: string, n: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + n * DAY_MS).toISOString().slice(0, 10);
}

/**
 * Days to try, oldest first: from the oldest day that still has stored
 * responses up to 3 days before today (Pacific), limited to those present in
 * the manifest with a release tag.
 */
export function daysToPrune(
  manifest: Manifest,
  oldestRawAt: string | null,
  now: Date,
  maxDays = 14,
): string[] {
  if (!oldestRawAt) return [];
  const last = addDays(pacificDate(now), -3);
  const out: string[] = [];
  for (let day = pacificDate(new Date(oldestRawAt)); day <= last; day = addDays(day, 1)) {
    const entry = manifest.days?.[day];
    if (entry && entry.release_tag && entry.files?.length) out.push(day);
    if (out.length >= maxDays) break;
  }
  return out;
}

export function assetUrl(repo: string, tag: string, name: string): string {
  return `https://github.com/${repo}/releases/download/${tag}/${name}`;
}
