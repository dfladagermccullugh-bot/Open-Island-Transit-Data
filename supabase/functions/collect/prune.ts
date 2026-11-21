// Prune: free space in the collector, one archived day at a time.
//
// A day's stored responses are deleted only when all of these hold:
//   1. the day is listed in the archive manifest on GitHub,
//   2. every file the manifest lists for it can be downloaded at its recorded size
//      (a one-byte ranged GET; GitHub's file host refuses HEAD requests),
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

/**
 * Total size from a Content-Range header such as "bytes 0-0/1158375".
 * Null when the header is absent or does not state a total.
 */
export function totalFromContentRange(header: string | null): number | null {
  const m = header ? /^bytes\s+\d+-\d+\/(\d+)$/.exec(header.trim()) : null;
  return m ? Number(m[1]) : null;
}

/**
 * True only when the response proves the file exists at the recorded size:
 * a 206 whose Content-Range total equals it. Anything else, including a
 * response that states no size, is not a confirmation.
 */
export function sizeConfirmed(status: number, contentRange: string | null, bytes: number): boolean {
  return status === 206 && totalFromContentRange(contentRange) === bytes;
}
