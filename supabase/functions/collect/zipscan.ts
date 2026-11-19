// Cheap structural check of a zip file without unpacking it: does it have a
// central directory, and which entries does it contain?
// The Passio static GTFS zip has been observed arriving without a central
// directory and without calendar files, so every download is checked.

export interface ZipScan {
  hasDirectory: boolean;
  files: string[];
}

const LOCAL = 0x04034b50; // "PK\x03\x04"
const CENTRAL = 0x02014b50; // "PK\x01\x02"
const EOCD = 0x06054b50; // "PK\x05\x06"

export function scanZip(buf: Uint8Array): ZipScan {
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const decoder = new TextDecoder();

  // The end-of-central-directory record sits in the last 22 + 65535 bytes.
  let eocd = -1;
  const stop = Math.max(0, buf.length - 22 - 65535);
  for (let i = buf.length - 22; i >= stop; i--) {
    if (view.getUint32(i, true) === EOCD) {
      eocd = i;
      break;
    }
  }

  // Preferred: read names from the central directory.
  if (eocd >= 0) {
    const count = view.getUint16(eocd + 10, true);
    let pos = view.getUint32(eocd + 16, true);
    const files: string[] = [];
    for (let n = 0; n < count; n++) {
      if (pos + 46 > buf.length || view.getUint32(pos, true) !== CENTRAL) break;
      const nameLen = view.getUint16(pos + 28, true);
      const extraLen = view.getUint16(pos + 30, true);
      const commentLen = view.getUint16(pos + 32, true);
      files.push(decoder.decode(buf.subarray(pos + 46, pos + 46 + nameLen)));
      pos += 46 + nameLen + extraLen + commentLen;
    }
    return { hasDirectory: true, files };
  }

  // No directory: walk the local headers as far as they go.
  const files: string[] = [];
  let pos = 0;
  while (pos + 30 <= buf.length && view.getUint32(pos, true) === LOCAL) {
    const flags = view.getUint16(pos + 6, true);
    const compSize = view.getUint32(pos + 18, true);
    const nameLen = view.getUint16(pos + 26, true);
    const extraLen = view.getUint16(pos + 28, true);
    if (pos + 30 + nameLen > buf.length) break;
    files.push(decoder.decode(buf.subarray(pos + 30, pos + 30 + nameLen)));
    // Bit 3: sizes live in a trailing data descriptor, so we cannot jump ahead.
    if (flags & 0x08) break;
    pos += 30 + nameLen + extraLen + compSize;
  }
  return { hasDirectory: false, files };
}
