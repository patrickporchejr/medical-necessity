import re
from datetime import date, timedelta

# Lookarounds, not \b: FHIR datetimes run straight into the "T" ("2022-01-15T00:23:12").
ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def shift_iso_dates(text: str, delta: timedelta) -> str:
    """Shift every YYYY-MM-DD in `text` by `delta`, leaving times and other text alone."""

    def shift(match: re.Match[str]) -> str:
        try:
            shifted = date(*map(int, match.groups())) + delta
        except ValueError:  # not a calendar date, e.g. 2022-13-45
            return match.group(0)
        return shifted.isoformat()

    return ISO_DATE.sub(shift, text)
