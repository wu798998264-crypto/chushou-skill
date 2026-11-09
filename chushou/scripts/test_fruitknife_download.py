"""Compatibility with the repository's existing CI; tests only the new route."""
import runpy
import sys
from pathlib import Path
sys.dont_write_bytecode = True
if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("test_hongguo_download.py")), run_name="__main__")
