"""Read retail barcodes (EAN-13/8, UPC-A/E) from photos with ZXing, so submissions can prove
which product they're about instead of taking the sender's word for it."""

import io

import zxingcpp
from PIL import Image, ImageOps

FORMATS = (zxingcpp.BarcodeFormat.EAN13, zxingcpp.BarcodeFormat.EAN8,
           zxingcpp.BarcodeFormat.UPCA, zxingcpp.BarcodeFormat.UPCE)


def _same(a: str, b: str) -> bool:
    """UPC-A 012345678905 is EAN-13 0012345678905: compare without leading zeros."""
    return a.lstrip("0") == b.lstrip("0")


def read(data: bytes) -> list[str]:
    """Barcode digits found in an image (already validated by llm.check_image)."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
    found = []
    # Phone photos are large; ZXing copes, but very large images are slow, so cap at 3000 px.
    image.thumbnail((3000, 3000))
    for result in zxingcpp.read_barcodes(image, formats=FORMATS):
        if result.valid and result.text.isdigit() and result.text not in found:
            found.append(result.text)
    return found


def matches(found: list[str], barcode: str) -> bool:
    return any(_same(code, barcode) for code in found)
