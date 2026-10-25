import io
import warnings

import pytest
import zxingcpp
from PIL import Image

from app import llm
from app.models import Nutrition, Product
from tests.test_api import client  # noqa: F401  (fixture)

BARCODE = "8901491503020"


def photo(barcode: str | None = None) -> bytes:
    """A JPEG 'pack photo', with an EAN-13 barcode on it if given."""
    canvas = Image.new("RGB", (900, 700), "white")
    if barcode:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            code = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.EAN13, barcode, width=500, height=200)
        canvas.paste(Image.fromarray(code).convert("RGB"), (200, 400))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=95)
    return out.getvalue()


@pytest.fixture
def reads_label(monkeypatch):
    async def fake(settings, schema, prompt, images=None, usage=None):
        return schema(name="Magic Masala", brand="Lay's",
                      ingredients_text="Potato, Edible vegetable oil, Spices, Salt, Acidity regulator (330), Colour (171)",
                      nutrition=Nutrition(energy_kcal=540, fat_g=33))
    monkeypatch.setattr(llm, "complete_json", fake)


def seed(client, **fields):
    svc = client.app.state.products
    product = Product(barcode=BARCODE, brand="Lay's", name="Magic Masala", source="off", **fields)

    async def save():
        await svc.store.save_product(product)
    client.portal.call(save)
    return svc.store


def submit(client, kind, photos, note=None):
    files = [("images", (f"{i}.jpg", p, "image/jpeg")) for i, p in enumerate(photos)]
    data = {"kind": kind, **({"note": note} if note else {})}
    return client.post(f"/products/{BARCODE}/submissions", files=files, data=data)


def test_photo_without_barcode_is_rejected(client, reads_label):
    seed(client)
    r = submit(client, "contribute", [photo()])
    assert r.status_code == 422 and "couldn't find a barcode" in r.json()["detail"]


def test_barcode_of_another_product_is_rejected(client, reads_label):
    seed(client)
    r = submit(client, "contribute", [photo("8901058015799")])
    assert r.status_code == 422 and "doesn't match" in r.json()["detail"]


def test_contribution_is_applied(client, reads_label):
    store = seed(client)
    r = submit(client, "contribute", [photo(), photo(BARCODE)])  # barcode in the second photo is enough
    assert r.status_code == 201
    body = r.json()
    assert body["submission"]["status"] == "applied" and body["submission"]["barcode"] == BARCODE
    assert body["verdict"]["product"]["ingredients_text"].startswith("Potato")
    saved = store.products[BARCODE]
    assert saved.source == "label" and not saved.verified and "E171" in saved.additives
    assert len(store.submission_images) == 2


def test_contribution_refused_when_list_exists(client, reads_label):
    seed(client, ingredients_text="Potato, oil, salt")
    assert submit(client, "contribute", [photo(BARCODE)]).status_code == 409


def test_challenge_is_kept_for_review(client, reads_label):
    store = seed(client, ingredients_text="Potato, oil, salt, Colour (110)", additives=["E110"])
    r = submit(client, "challenge", [photo(BARCODE)], note="The pack I have lists different colours")
    assert r.status_code == 201
    sub = r.json()["submission"]
    assert sub["status"] == "pending_review" and r.json()["verdict"] is None
    assert sub["added_codes"] == ["E330", "E171"] and sub["removed_codes"] == ["E110"]
    assert sub["note"] == "The pack I have lists different colours"
    assert store.products[BARCODE].ingredients_text == "Potato, oil, salt, Colour (110)"  # unchanged
    assert list(store.submissions.values())[0].current_ingredients == "Potato, oil, salt, Colour (110)"
