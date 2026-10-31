import hashlib
from typing import Literal

from pydantic import BaseModel, Field, computed_field

Region = Literal["IN", "EU", "US"]
Source = Literal["label", "off", "curated", "web"]


class Nutrition(BaseModel):
    """Values per 100 g (solids) or per 100 ml (liquids)."""

    energy_kcal: float | None = None
    carbs_g: float | None = None
    sugars_g: float | None = None
    added_sugars_g: float | None = None
    fat_g: float | None = None
    saturated_fat_g: float | None = None
    trans_fat_g: float | None = None
    protein_g: float | None = None
    sodium_mg: float | None = None


class Evidence(BaseModel):
    """Where web-sourced data came from; the quote is verified to appear on the page."""

    source_url: str
    ingredients_quote: str


class Product(BaseModel):
    barcode: str | None = None
    name: str | None = None
    brand: str | None = None
    ingredients_text: str | None = None
    additives: list[str] = Field(default_factory=list, description="Normalised codes, e.g. E951")
    nutrition: Nutrition = Field(default_factory=Nutrition)
    claims: list[str] = Field(default_factory=list, description="Marketing claims as printed on the pack")
    is_liquid: bool | None = None
    source: Source
    verified: bool = False
    evidence: Evidence | None = None
    image_file_id: str | None = Field(default=None, description="Pack photo in Appwrite Storage")
    image_url: str | None = Field(default=None, description="External pack photo, e.g. from Open Food Facts")

    @computed_field  # included in API responses so clients can link to products without a barcode
    @property
    def key(self) -> str:
        """Store key: the barcode, or a hash of brand + name for products found without one."""
        if self.barcode:
            return self.barcode
        ident = f"{self.brand or ''}|{self.name or ''}".lower().strip()
        return "n-" + hashlib.sha1(ident.encode()).hexdigest()[:20]

    @property
    def embed_text(self) -> str:
        return " ".join(x for x in (self.brand, self.name) if x)


class ClaimFinding(BaseModel):
    claim: str
    rule: str
    actual: float | None
    threshold: float
    unit: str
    passes: bool | None
    message: str
    source_url: str
    source_verified: bool


class RegionStatus(BaseModel):
    """A substance's status in one region. Anything but "unknown" is backed by `quote`,
    verified to appear on `source_url`."""

    status: Literal["banned", "restricted", "allowed", "unknown"]
    since: str | None = None
    reason: str | None = None
    fact: str | None = None  # plain-language summary written from the quote
    quote: str | None = None
    source_url: str | None = None


class AdditiveFinding(BaseModel):
    id: str
    code: str  # E-number, or "" for substances without one (e.g. trans fats)
    name: str
    category: str
    matched: str  # the code or ingredient name found on the product
    status: dict[Region, RegionStatus]
    flagged: bool  # banned in at least one region
    message: str
    origin: Literal["seed", "auto"] = "seed"  # "auto": researched automatically after a scan found it
    researched_at: str | None = None


class LimitCheck(BaseModel):
    """A product number compared with a legal limit. Informational: never flagged."""

    name: str
    region: Region
    actual: float
    limit: float
    unit: str
    result: Literal["within", "above"]
    message: str
    source_url: str


class Ingredient(BaseModel):
    """One entry of the ingredients list, for display."""

    name: str
    percent: str | None = None
    codes: list[str] = Field(default_factory=list)
    sub: list["Ingredient"] = Field(default_factory=list)
    section: str | None = None  # e.g. "Noodles", "Masala Tastemaker" on multi-part packs
    flagged: bool = False  # contains a substance banned in at least one region
    flag_reason: str | None = None


class OtherAdditive(BaseModel):
    """A code on the label that isn't in our researched list (yet)."""

    code: str
    name: str | None  # from the Open Food Facts additive taxonomy
    researching: bool = False  # research started now; results show on a later scan


class Counterpart(BaseModel):
    region: Region
    country: str
    product_name: str
    ingredients_quote: str
    source_url: str
    additives: list[str] = Field(default_factory=list)
    only_in_india: list[str] = Field(default_factory=list)
    only_in_counterpart: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    product: Product
    data_issues: list[str]
    claims: list[ClaimFinding]
    additives: list[AdditiveFinding]
    counterpart: Counterpart | None = None
    headline: list[str]
    other_additives: list[OtherAdditive] = Field(default_factory=list)
    ingredients: list[Ingredient] = Field(default_factory=list)
    limits: list[LimitCheck] = Field(default_factory=list)


class Submission(BaseModel):
    """A shopper's photo of a pack: adds an ingredient list (contribute) or disputes ours
    (challenge). The barcode is read from the photo, not typed in."""

    id: str
    product_key: str
    kind: Literal["contribute", "challenge"]
    status: Literal["applied", "pending_review", "accepted", "rejected"]
    barcode: str  # read from the photo
    note: str | None = None
    current_ingredients: str | None = None
    proposed: Product | None = None  # what we read from the photo
    added_codes: list[str] = Field(default_factory=list)
    removed_codes: list[str] = Field(default_factory=list)
    image_file_ids: list[str] = Field(default_factory=list)
    created_at: str


class ProductSummary(BaseModel):
    """A catalog entry for the product list, with what it needs from shoppers."""

    key: str
    barcode: str | None
    brand: str | None
    name: str | None
    source: Source
    verified: bool
    status: Literal["needs_ingredients", "data_looks_wrong", "checked", "ok"]
    has_image: bool = False
    image_version: str | None = None  # changes when the photo does, so clients can bust caches
    needs_help: bool  # someone with the pack can add or correct its ingredient list
