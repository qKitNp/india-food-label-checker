import io

import pytest
from PIL import Image

from app import llm


def png(width: int, height: int, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), "orange")
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    image.save(out, "JPEG" if orientation else "PNG", exif=exif)
    return out.getvalue()


def test_shrink_downscales_to_jpeg():
    out = Image.open(io.BytesIO(llm.shrink(png(3000, 4000))))
    assert out.format == "JPEG" and max(out.size) == llm.MAX_IMAGE_SIDE


def test_shrink_keeps_small_images():
    assert Image.open(io.BytesIO(llm.shrink(png(800, 600)))).size == (800, 600)


def test_shrink_applies_exif_rotation():
    # Orientation 6 = stored sideways; a 4000x3000 landscape file displays as portrait.
    out = Image.open(io.BytesIO(llm.shrink(png(4000, 3000, orientation=6))))
    assert out.size[1] > out.size[0]


def test_shrink_rejects_garbage():
    with pytest.raises(llm.InvalidImage):
        llm.shrink(b"not an image")
