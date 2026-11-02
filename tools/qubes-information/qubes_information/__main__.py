"""``python3 -m qubes_information <qube>`` opens the window (see ``frontend``)."""

import sys

from .frontend import main

sys.exit(main())
