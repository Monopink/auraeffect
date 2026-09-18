import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auraeffect.cli import main as cli_main
from auraeffect.gui import main as gui_main


raise SystemExit(cli_main() if len(sys.argv) > 1 else gui_main())
