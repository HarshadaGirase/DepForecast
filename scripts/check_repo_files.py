"""Fail if any tracked file is over 50 MB or is a model-weight file (SPEC §15)."""

import subprocess
import sys
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
WEIGHT_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf"}


def main() -> int:
    output = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True, text=True
    ).stdout
    files = [name for name in output.split("\0") if name]
    problems = []
    for name in files:
        path = Path(name)
        if path.suffix.lower() in WEIGHT_EXTENSIONS:
            problems.append(f"{name}: model-weight file must not be committed")
        elif path.is_file() and path.stat().st_size > MAX_BYTES:
            problems.append(f"{name}: {path.stat().st_size / 1e6:.1f} MB is over the 50 MB limit")
    for problem in problems:
        print(f"ERROR {problem}")
    if not problems:
        print(f"OK: {len(files)} tracked files, none too large, no weight files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
