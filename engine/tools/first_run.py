"""Export a deterministic, ready-to-queue ComfyUI product-photo request."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.errors import WorkflowEngineError
from engine.registry import Registry
from engine.render import RenderOptions, render_file

SAMPLE_WORKFLOWS = ("t2i_v1", "flux2_klein_t2i_v1")
DEFAULT_SEED = 20260916


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", choices=SAMPLE_WORKFLOWS, default=SAMPLE_WORKFLOWS[0])
    parser.add_argument("--prompt", help="Override the sample product-photo prompt")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seed < 0:
        parser.error("--seed must be non-negative")

    try:
        registry = Registry.load()
        entry = registry.require(args.workflow)
        if not (entry.is_enabled and entry.is_gpu_verified and entry.render_path_l4):
            parser.error(f"{entry.id} is unavailable for the first-run sample")
        result = render_file(
            registry.definition_path(entry),
            entry.schema,
            {"prompt": args.prompt, "seed": args.seed},
            options=RenderOptions(strict_placeholders=True),
        )
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump({"prompt": result.workflow}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (WorkflowEngineError, OSError) as exc:
        parser.error(str(exc))
    print(f"Saved {args.output}. Required models: {', '.join(entry.models)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
