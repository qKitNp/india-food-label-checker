// Retail barcode detection: the browser's BarcodeDetector where available, otherwise the
// ZXing WebAssembly ponyfill (iOS Safari, Firefox), loaded only when needed.

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e']

export type Detector = { detect(source: ImageBitmapSource): Promise<{ rawValue: string }[]> }

let detector: Promise<Detector> | null = null

export function getDetector(): Promise<Detector> {
  detector ??= (async () => {
    const native = (globalThis as { BarcodeDetector?: new (o: { formats: string[] }) => Detector }).BarcodeDetector
    if (native) return new native({ formats: FORMATS })
    const { BarcodeDetector } = await import('barcode-detector/ponyfill')
    return new BarcodeDetector({ formats: FORMATS as never }) as unknown as Detector
  })()
  return detector
}

/** Barcode digits found in a photo; empty if none or if the photo can't be decoded. */
export async function barcodesIn(photo: Blob): Promise<string[]> {
  try {
    const [d, bitmap] = await Promise.all([getDetector(), createImageBitmap(photo)])
    const found = await d.detect(bitmap)
    return [...new Set(found.map((f) => f.rawValue.replace(/\D/g, '')).filter((v) => v.length >= 8))]
  } catch {
    return []
  }
}
