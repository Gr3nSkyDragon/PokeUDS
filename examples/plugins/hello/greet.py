"""Hello's tool: a plugin tool is a script the app runs as a child process (docs/plugins.md, A tool's script)."""

import argparse

from pokeuds import status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--greeting", default="Hello")
    parser.add_argument("--times", type=int, default=1)
    parser.add_argument("--shout", action="store_true")
    parser.add_argument("--trainer", default="trainer")
    args = parser.parse_args()
    for _ in range(max(1, args.times)):
        line = f"{args.greeting}, {args.trainer}!"
        print(line.upper() if args.shout else line)
    status.emit("note", text="Hello said hello")          # a note in the session panel
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
