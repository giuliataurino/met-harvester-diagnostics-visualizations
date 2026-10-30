"""The Met object schema as documented at https://metmuseum.github.io/#object, plus where
each field lands in the staging mapping (staging.py). This is the reference side of the
field map; survey.py supplies the observed side, mapping.py joins them.

Documented type is the API documentation's word, not what the API actually sends: e.g.
accessionYear is documented "string" but MetObject coerces it to int, and dimensionsParsed
is documented "float" but is a list of dicts. The survey records what was actually seen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FieldSpec:
    name: str
    doc_type: str            # type as written in the API docs
    group: str               # our grouping, for the report
    note: str                # abbreviated note from the docs
    staging: tuple[str, ...] = ()   # stg: predicates this field feeds (empty = not mapped)
    aat: Optional[str] = None       # aat.py category looked up for this field, if any
    children: tuple[str, ...] = ()  # documented sub-keys for array-of-object fields


def _f(name, doc_type, group, note, staging=(), aat=None, children=()):
    if isinstance(staging, str):
        staging = (staging,)
    return FieldSpec(name, doc_type, group, note, tuple(staging), aat, tuple(children))


DOCUMENTED: tuple[FieldSpec, ...] = (
    # identity / flags
    _f("objectID", "int", "identity", "Unique key", "stg:objectid"),
    _f("isHighlight", "boolean", "flags", "Popular/important work", "stg:isHighlight"),
    _f("isPublicDomain", "boolean", "flags", "Public domain", "stg:isPublicDomain"),
    _f("isTimelineWork", "boolean", "flags", "On the Timeline of Art History", "stg:isTimelineWork"),
    _f("accessionNumber", "string", "identity", "Not always unique", "stg:objectnumber"),
    _f("accessionYear", "string", "identity", "Year acquired (model coerces to int)", "stg:accessionyear"),
    # images
    _f("primaryImage", "string", "images", "URL, JPEG", ("stg:primaryImage", "stg:hasImage")),
    _f("primaryImageSmall", "string", "images", "Lower-res URL", "stg:primaryImageSmall"),
    _f("additionalImages", "array", "images", "Array of URLs", "stg:hasImage"),
    # people (structured)
    _f("constituents", "array", "people", "Role, name, ULAN, Wikidata, gender",
       ("stg:hasPerson", "stg:personName", "stg:personRole", "stg:personGender", "stg:ulanId", "stg:wikidataId"),
       children=("constituentID", "role", "name", "constituentULAN_URL", "constituentWikidata_URL", "gender")),
    # institutional
    _f("department", "string", "institutional", "Curatorial department", "stg:department"),
    _f("objectName", "string", "typing", "Physical type", ("stg:typeLabel", "stg:aatType"), aat="object_types"),
    _f("title", "string", "naming", "Title or identifying phrase", "stg:title"),
    _f("culture", "string", "typing", "Culture or people; often compound", ("stg:culture", "stg:cultureAAT"), aat="cultures"),
    _f("period", "string", "dating", "Period phrase", "stg:periodLabel"),
    _f("dynasty", "string", "dating", "Dynasty", "stg:dynasty"),
    _f("reign", "string", "dating", "Reign", "stg:reign"),
    _f("portfolio", "string", "naming", "Series / portfolio", "stg:portfolio"),
    # people (flat primary-artist block)
    _f("artistRole", "string", "people", "Role of primary artist", "stg:personRole"),
    _f("artistPrefix", "string", "people", "Attribution qualifier", "stg:personPrefix"),
    _f("artistDisplayName", "string", "people", "Display-order name", ("stg:personDisplayName", "stg:personName")),
    _f("artistDisplayBio", "string", "people", "Nationality and life dates", "stg:personDisplayBio"),
    _f("artistSuffix", "string", "people", "Role qualifier", "stg:personSuffix"),
    _f("artistAlphaSort", "string", "people", "Sort form", "stg:personAlphaSort"),
    _f("artistNationality", "string", "people", "Nationality", "stg:personCulture"),
    _f("artistBeginDate", "string", "people", "Birth year (string)", "stg:personDateBegin"),
    _f("artistEndDate", "string", "people", "Death year (string)", "stg:personDateEnd"),
    _f("artistGender", "string", "people", "Currently female designations only", "stg:personGender"),
    _f("artistWikidata_URL", "string", "people", "Wikidata URL", "stg:wikidataId"),
    _f("artistULAN_URL", "string", "people", "ULAN URL", "stg:ulanId"),
    # dating
    _f("objectDate", "string", "dating", "Display date phrase", "stg:dated"),
    _f("objectBeginDate", "int", "dating", "Machine-readable start year", "stg:datebegin"),
    _f("objectEndDate", "int", "dating", "Machine-readable end year", "stg:dateend"),
    # physical
    _f("medium", "string", "physical", "Materials", ("stg:material", "stg:materialAAT"), aat="materials"),
    _f("dimensions", "string", "physical", "Display dimensions", "stg:dimensions"),
    _f("dimensionsParsed", "float", "physical", "Docs say float; actually list of {element,dimensionType,dimension}",
       "stg:hasMeasurement", children=("element", "dimensionType", "dimension")),
    _f("measurements", "array", "physical", "Elements with cm / kg values",
       ("stg:hasMeasurement", "stg:measurementType", "stg:measurementValue", "stg:measurementUnit",
        "stg:measurementElement", "stg:measurementDescription"),
       children=("elementName", "elementDescription", "elementMeasurements")),
    _f("creditLine", "string", "institutional", "Credit line", "stg:creditline"),
    # geography
    _f("geographyType", "string", "geography", "Made in / From / Attributed to", "stg:geographyType"),
    _f("city", "string", "geography", "", ("stg:city", "stg:placeLabel")),
    _f("state", "string", "geography", "", ("stg:state", "stg:placeLabel")),
    _f("county", "string", "geography", "", ("stg:county", "stg:placeLabel")),
    _f("country", "string", "geography", "", ("stg:country", "stg:placeLabel")),
    _f("region", "string", "geography", "Frequently null", ("stg:region", "stg:placeLabel")),
    _f("subregion", "string", "geography", "Frequently null", ("stg:subregion", "stg:placeLabel")),
    _f("locale", "string", "geography", "Frequently null", ("stg:locale", "stg:placeLabel")),
    _f("locus", "string", "geography", "Frequently null", ("stg:locus", "stg:placeLabel")),
    _f("excavation", "string", "geography", "Excavation name and dates", "stg:excavation"),
    _f("river", "string", "geography", "Frequently null", "stg:river"),
    # typing / rights / links
    _f("classification", "string", "typing", "General type", ("stg:classification", "stg:typeLabel", "stg:aatType"),
       aat="classifications"),
    _f("rightsAndReproduction", "string", "rights", "Copyright credit", ("stg:rightsStatement", "stg:copyright")),
    _f("linkResource", "string", "links", "Object page URL (often empty)", "stg:linkResource"),
    _f("metadataDate", "datetime", "institutional", "Last metadata update", "stg:metadataDate"),
    _f("repository", "string", "institutional", "Repository", "stg:repository"),
    _f("objectURL", "string", "links", "Object page URL", "stg:catalogPage"),
    _f("tags", "array", "subjects", "Subject keywords with AAT / Wikidata URLs",
       ("stg:subject", "stg:subjectAAT", "stg:subjectWikidata"), children=("term", "AAT_URL", "Wikidata_URL")),
    _f("objectWikidata_URL", "string", "links", "Wikidata URL for the object", "stg:wikidataId"),
    _f("GalleryNumber", "string", "institutional", "Gallery, where on view", "stg:galleryNumber"),
)

BY_NAME: dict[str, FieldSpec] = {f.name: f for f in DOCUMENTED}

# Paths the survey will produce for documented nested fields, so they count as documented too.
DOCUMENTED_PATHS: set[str] = set(BY_NAME)
for _spec in DOCUMENTED:
    for _child in _spec.children:
        DOCUMENTED_PATHS.add(f"{_spec.name}[].{_child}")
    if _spec.name in ("additionalImages",):
        DOCUMENTED_PATHS.add(f"{_spec.name}[]")
# elementMeasurements is a free-form {measureName: number} map; its keys are data, not schema.
DYNAMIC_KEY_PARENTS: set[str] = {"measurements[].elementMeasurements"}

assert len(DOCUMENTED) == 58, len(DOCUMENTED)
