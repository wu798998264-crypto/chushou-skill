"""Legacy CI filename only; delegates to media setup, contains no downloader."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chushou_runtime import main
if __name__ == "__main__":
    sys.exit(main())
