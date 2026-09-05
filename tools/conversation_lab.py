"""Run the text lab against either isolated server checkout, without app import."""
import argparse
from pathlib import Path
import sys

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--server-root", type=Path, default=Path(__file__).resolve().parents[1] / "server")
    args, remaining = parser.parse_known_args()
    sys.path.insert(0, str(args.server_root.resolve()))
    from services.conversation_text_lab import main
    raise SystemExit(main(remaining))
