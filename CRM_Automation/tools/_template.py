"""Tool template — copy this to create new tools.

Usage:
    python tools/my_tool.py --arg1 value1

Output:
    Prints JSON result to stdout.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env
from shared.logger import info, error
from shared.cost_tracker import check_budget
from shared.sandbox import validate_write_path


def main():
    parser = argparse.ArgumentParser(description="Tool description")
    parser.add_argument("--arg1", required=True, help="Description")
    args = parser.parse_args()

    load_env()
    check_budget(estimated_cost=0.0)

    result = {"status": "success"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
