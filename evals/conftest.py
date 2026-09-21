import sys
from pathlib import Path

# The LangSmith capture fixture lives with the api tests; the evals reuse it as is.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from tests.conftest import ls  # noqa: E402,F401
