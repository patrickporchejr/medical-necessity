import os

# Graph nodes emit Logfire spans; where nothing configured Logfire, that is a silent no-op.
os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")
