"""The legal acts Kalys covers. Codes come from docs/cbd-api.md (verified 2026-10-05)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Act:
    slug: str  # short name we use in files and in the database
    document_code: str  # the code GetDocument accepts, for example "3-38"
    name_en: str


ACTS = [
    Act("constitution", "1-2", "Constitution of the Kyrgyz Republic (2021)"),
    Act("criminal_code", "3-38", "Criminal Code of the Kyrgyz Republic (2021, No. 127)"),
    Act(
        "criminal_procedure_code",
        "3-37",
        "Criminal Procedure Code of the Kyrgyz Republic (2021, No. 129)",
    ),
]

# Language codes used by the API. Note: Kyrgyz is "kg" in this API, not the ISO code "ky".
LANGUAGES = ["ru", "kg"]
