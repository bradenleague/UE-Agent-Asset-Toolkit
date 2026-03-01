#!/usr/bin/env python3
"""Backwards-compatible shim — delegates to unreal_agent.cli.main()."""

from unreal_agent.cli import main

if __name__ == "__main__":
    main()
