"""De-identification of tool results before they reach the model.

Structured records are handled field by field and fail closed: a field the scrubber
has not been told about raises rather than passing through. Free text goes through
Presidio, driven by names learned from the structured fields rather than by NER (see
`_analyzer`). Dates are shifted by a per-patient offset, never redacted, so durations
such as "at least 90 days of methotrexate" still compute.
"""

from functools import cache
from typing import Any

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    EmailRecognizer,
    PhoneRecognizer,
    UsSsnRecognizer,
)
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from app.phi.dates import ISO_DATE, shift_iso_dates
from app.phi.vault import Vault

NAME_FIELDS = {"name", "author"}
TEXT_FIELDS = {"text", "dosage", "reason"}
DATE_FIELDS = {"birth_date", "onset_date", "recorded_date", "authored_on", "effective_date", "date"}
SAFE_FIELDS = {"resource_type", "status", "gender", "clinical_status", "verification_status", "unit"}
CODE_FIELDS = {"code", "type"}
CODE_KEYS = {"system", "code", "display"}

TITLES = {"dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "mx", "mx."}
MIN_NAME_LENGTH = 3
ID_KINDS = ("PATIENT", "CONDITION", "MEDICATIONREQUEST", "OBSERVATION", "DOCUMENTREFERENCE")

# Presidio entity -> vault placeholder kind
PATTERN_ENTITIES = {"PHONE_NUMBER": "PHONE", "EMAIL_ADDRESS": "EMAIL", "US_SSN": "SSN"}


class UnclassifiedField(ValueError):
    """A tool result carried a field the scrubber has no rule for."""


@cache
def _analyzer() -> AnalyzerEngine:
    # No NER. Measured on the cohort's notes: spaCy missed the patient's first name in
    # 100 of 100 notes (names like "Abel832" do not look like names) and flagged clinical
    # text ("Social History\nPatient", drug names) as people and places. Names come from
    # the vault's deny-list; Presidio contributes precise pattern recognizers.
    registry = RecognizerRegistry(supported_languages=["en"])
    for recognizer in (PhoneRecognizer(), EmailRecognizer(), UsSsnRecognizer()):
        registry.add_recognizer(recognizer)
    nlp = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    ).create_engine()
    return AnalyzerEngine(registry=registry, nlp_engine=nlp, supported_languages=["en"])


class Anonymizer:
    def __init__(self, vault: Vault):
        self.vault = vault
        self._engine = AnonymizerEngine()

    def pseudonym(self, kind: str, original: str) -> str:
        return self.vault.placeholder(kind, original)

    def register_name(self, name: str) -> str:
        """Placeholder for a full name, token by token, so a lone first name in a note maps
        to the same placeholder as the same token inside the full name."""
        return " ".join(
            token if token.lower() in TITLES else self.vault.placeholder("NAME", token)
            for token in name.split()
        )

    def scrub_result(self, payload: Any) -> Any:
        """Scrub a tool's structured result: a record, a list of records, or {"result": ...}."""
        if isinstance(payload, list):
            return [self.scrub_result(item) for item in payload]
        if isinstance(payload, dict) and "resource_type" in payload:
            return self._scrub_record(payload)
        if isinstance(payload, dict) and set(payload) == {"result"}:
            return {"result": self.scrub_result(payload["result"])}
        raise UnclassifiedField(f"Unrecognized result shape: {type(payload).__name__}")

    def scrub_text(self, text: str, patient_id: str) -> str:
        text = self._mask_known_ids(text)
        known = {
            name.lower(): name
            for name in self.vault.originals("NAME")
            if len(name) >= MIN_NAME_LENGTH
        }
        ad_hoc = (
            [PatternRecognizer("NAME", deny_list=list(known.values()))] if known else []
        )
        findings = _analyzer().analyze(
            text=text,
            language="en",
            entities=["NAME", *PATTERN_ENTITIES],
            ad_hoc_recognizers=ad_hoc,
        )
        operators = {
            "NAME": OperatorConfig(
                "custom",
                {"lambda": lambda t: self.vault.placeholder("NAME", known.get(t.lower(), t))},
            ),
            **{
                entity: OperatorConfig(
                    "custom", {"lambda": lambda t, kind=kind: self.vault.placeholder(kind, t)}
                )
                for entity, kind in PATTERN_ENTITIES.items()
            },
        }
        text = self._engine.anonymize(text, findings, operators).text
        return shift_iso_dates(text, self.vault.date_offset(patient_id))

    def scrub_message(self, text: str) -> str:
        """For error text, where no patient is in scope: dates are blanked, not shifted."""
        text = self._mask_known_ids(text)
        for name, token in self.vault.originals("NAME").items():
            text = text.replace(name, token)
        return ISO_DATE.sub("<DATE>", text)

    def _mask_known_ids(self, text: str) -> str:
        for kind in ID_KINDS:
            for original, token in self.vault.originals(kind).items():
                text = text.replace(original, token)
        return text

    def _scrub_record(self, record: dict[str, Any]) -> dict[str, Any]:
        kind = record["resource_type"]
        patient_id = record["id"] if kind == "Patient" else record.get("patient_id")
        if patient_id is None:
            raise UnclassifiedField(f"{kind} record has no patient scope")

        # Learn names first so free text in the same record is scrubbed against them.
        for field in NAME_FIELDS & record.keys():
            if record[field]:
                self.register_name(record[field])

        out: dict[str, Any] = {}
        for key, value in record.items():
            if value is None:
                out[key] = None
            elif key == "id":
                out[key] = self.pseudonym(kind.upper(), value)
            elif key == "patient_id":
                out[key] = self.pseudonym("PATIENT", value)
            elif key in NAME_FIELDS:
                out[key] = self.register_name(value)
            elif key in TEXT_FIELDS:
                out[key] = self.scrub_text(value, patient_id)
            elif key in DATE_FIELDS:
                out[key] = shift_iso_dates(value, self.vault.date_offset(patient_id))
            elif key in SAFE_FIELDS:
                out[key] = value
            elif key in CODE_FIELDS:
                out[key] = self._scrub_code(key, value)
            elif key == "value":
                out[key] = self.scrub_text(value, patient_id) if isinstance(value, str) else value
            else:
                raise UnclassifiedField(f"No PHI rule for field {key!r} on {kind}")
        return out

    @staticmethod
    def _scrub_code(field: str, value: dict[str, Any]) -> dict[str, Any]:
        unknown = value.keys() - CODE_KEYS
        if unknown:
            raise UnclassifiedField(f"No PHI rule for {field}.{sorted(unknown)[0]}")
        return value
