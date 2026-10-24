// Shrink photos on the device before upload: phone photos are several MB, and the server
// downsizes to 1600 px anyway. Also fixes orientation, which browsers apply when decoding.

const MAX_SIDE = 1600

/** A photo the browser can't decode (e.g. an iPhone HEIC file in Chrome) or a non-image file. */
export class UnreadablePhoto extends Error {
  constructor() {
    super("We can't read that file. Take a photo with the camera, or choose a JPEG or PNG image.")
  }
}

export async function toJpeg(source: Blob | HTMLVideoElement, maxSide = MAX_SIDE): Promise<Blob> {
  let width: number
  let height: number
  let draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void

  if (source instanceof HTMLVideoElement) {
    width = source.videoWidth
    height = source.videoHeight
    draw = (ctx, w, h) => ctx.drawImage(source, 0, 0, w, h)
  } else {
    if (source.type && !source.type.startsWith('image/')) throw new UnreadablePhoto()
    let bitmap: ImageBitmap
    try {
      bitmap = await createImageBitmap(source, { imageOrientation: 'from-image' })
    } catch {
      throw new UnreadablePhoto()
    }
    width = bitmap.width
    height = bitmap.height
    draw = (ctx, w, h) => ctx.drawImage(bitmap, 0, 0, w, h)
  }

  const scale = Math.min(1, maxSide / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas not available')
  draw(ctx, canvas.width, canvas.height)
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not encode photo'))), 'image/jpeg', 0.85),
  )
}
