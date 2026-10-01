"""Console entry point: a Qt system library the host lacks must not surface as a traceback."""

from __future__ import annotations

import logging


def main() -> int:
    try:
        from refrain.app import main as run
    except ImportError as e:
        from refrain import qt_libraries

        missing = qt_libraries.missing_from_import_error(str(e))
        if not missing:
            raise
        # Logging is not set up this early, so logging.lastResort would put
        # the same message on stderr a second time.
        logging.getLogger("refrain").addHandler(logging.NullHandler())
        qt_libraries.report(missing)
        return 1
    return run()
