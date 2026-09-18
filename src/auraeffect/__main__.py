import sys

from .cli import main as cli_main
from .gui import main as gui_main

raise SystemExit(cli_main() if len(sys.argv) > 1 else gui_main())
