#!/usr/bin/env python3
"""
Fail unless the release tag matches the version in pyproject.toml.

The tag comes from the GITHUB_REF_NAME environment variable (e.g. "v1.3.0");
a leading "v" is ignored. Needs Python 3.11+ for tomllib.
"""

import os
import sys
import tomllib


def main() -> int:
    tag = os.environ.get("GITHUB_REF_NAME", "")
    with open("pyproject.toml", "rb") as f:
        version = tomllib.load(f)["project"]["version"]

    if tag.removeprefix("v") != version:
        print(
            f"::error::Tag {tag!r} does not match pyproject.toml version {version!r}. "
            "Bump the version (or retag) before releasing."
        )
        return 1
    print(f"Tag {tag} matches pyproject.toml version {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
