"""Install a bundle after the run-scoped AgentConfig selects its plugin store."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--bundle", required=True)
    args = parser.parse_args()

    from datus.configuration.agent_config_loader import load_agent_config

    load_agent_config(config=args.config, reload=True, create_if_missing=False)
    from datus.cli import plugin_service

    result = plugin_service.install(f"zip:{args.bundle}", force=True)
    print(json.dumps(result.__dict__, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
