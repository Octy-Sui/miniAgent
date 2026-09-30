#!/usr/bin/env python3
"""Generate or verify n-wise (each-choice, pairwise, 3-wise, ...) test combinations.

Usage:
  python nwise.py model.json [--strength N] [--format markdown|matrix|tree|json]
  python nwise.py model.json --verify tests.json [--strength N]

Model file (JSON):
  {
    "parameters": {"Browser": ["Chrome", "Firefox", "Safari"], "OS": ["Windows", "macOS"]},
    "constraints": [{"Browser": "Safari", "OS": "Windows"}],
    "strength": 2
  }

Each constraint is a forbidden partial combination: no test may contain all of its
parameter values at once. Tests file for --verify: a JSON list of objects mapping
every parameter to a value, or {"tests": [...]}.

Standard library only. Output is deterministic for the same model.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

Tuple = tuple[tuple[str, str], ...]


def load_model(
    path: Path,
) -> tuple[dict[str, list[str]], list[dict[str, str]], int | None]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_params = data.get("parameters")
    if not isinstance(raw_params, dict) or not raw_params:
        raise ValueError("model needs a non-empty 'parameters' object")

    params: dict[str, list[str]] = {}
    for name, values in raw_params.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"parameter {name!r} needs a non-empty list of values")
        params[str(name)] = [str(value) for value in values]

    constraints = [
        {str(k): str(v) for k, v in c.items()} for c in data.get("constraints", [])
    ]
    for constraint in constraints:
        for name, value in constraint.items():
            if name not in params or value not in params[name]:
                raise ValueError(
                    f"constraint {constraint} uses unknown parameter or value {name}={value}"
                )

    return params, constraints, data.get("strength")


def violates(assignment: dict[str, str], constraints: list[dict[str, str]]) -> bool:
    """True when the (partial) assignment contains every value of some forbidden combination."""
    return any(all(assignment.get(k) == v for k, v in c.items()) for c in constraints)


def required_tuples(
    params: dict[str, list[str]], constraints: list[dict[str, str]], strength: int
) -> list[Tuple]:
    names = list(params)
    tuples: list[Tuple] = []
    for combo in itertools.combinations(names, strength):
        for values in itertools.product(*(params[n] for n in combo)):
            candidate = tuple(zip(combo, values))
            if not violates(dict(candidate), constraints):
                tuples.append(candidate)
    return tuples


def tuples_in(test: dict[str, str], names: list[str], strength: int) -> set[Tuple]:
    return {
        tuple((n, test[n]) for n in combo)
        for combo in itertools.combinations(names, strength)
    }


def complete(
    seed: dict[str, str],
    params: dict[str, list[str]],
    constraints: list[dict[str, str]],
    uncovered: set[Tuple],
    strength: int,
) -> dict[str, str] | None:
    """Extend a partial assignment to a full test, greedily covering as many uncovered tuples as possible.

    Returns None when the constraints make the seed impossible to complete.
    """
    names = list(params)
    order = [n for n in names if n not in seed]

    def gain(assignment: dict[str, str], name: str, value: str) -> int:
        trial = {**assignment, name: value}
        others = [n for n in names if n in trial and n != name]
        count = 0
        for combo in itertools.combinations(others, strength - 1):
            members = sorted((*combo, name), key=names.index)
            if tuple((n, trial[n]) for n in members) in uncovered:
                count += 1
        return count

    def search(assignment: dict[str, str], index: int) -> dict[str, str] | None:
        if index == len(order):
            return dict(assignment)
        name = order[index]
        values = params[name]
        for value in sorted(
            values, key=lambda v: (-gain(assignment, name, v), values.index(v))
        ):
            assignment[name] = value
            if not violates(assignment, constraints):
                result = search(assignment, index + 1)
                if result is not None:
                    return result
            del assignment[name]
        return None

    if violates(seed, constraints):
        return None
    return search(dict(seed), 0)


def generate(
    params: dict[str, list[str]],
    constraints: list[dict[str, str]],
    strength: int,
    candidates: int = 20,
) -> tuple[list[dict[str, str]], list[Tuple], list[Tuple]]:
    """Return (tests, required tuples, tuples that the constraints make impossible)."""
    names = list(params)
    ordered = required_tuples(params, constraints, strength)
    uncovered = set(ordered)
    impossible: list[Tuple] = []
    tests: list[dict[str, str]] = []

    while uncovered:
        best: dict[str, str] | None = None
        best_cover: set[Tuple] = set()
        tried = 0
        for seed in ordered:
            if seed not in uncovered:
                continue
            if tried >= candidates:
                break
            test = complete(dict(seed), params, constraints, uncovered, strength)
            if test is None:
                impossible.append(seed)
                uncovered.discard(seed)
                continue
            tried += 1
            cover = tuples_in(test, names, strength) & uncovered
            if len(cover) > len(best_cover):
                best, best_cover = test, cover
        if best is None:
            break
        tests.append(best)
        uncovered -= best_cover

    return tests, ordered, impossible


def verify(
    tests: list[dict[str, str]],
    params: dict[str, list[str]],
    constraints: list[dict[str, str]],
    strength: int,
) -> dict:
    names = list(params)
    problems: list[str] = []
    covered: set[Tuple] = set()
    for index, test in enumerate(tests, start=1):
        unknown = [
            f"{k}={v}" for k, v in test.items() if k not in params or v not in params[k]
        ]
        missing = [n for n in names if n not in test]
        if unknown or missing:
            problems.append(
                f"test {index}: unknown {unknown or '-'}; missing parameters {missing or '-'}"
            )
            continue
        if violates(test, constraints):
            problems.append(f"test {index}: violates a constraint")
            continue
        covered |= tuples_in(test, names, strength)

    required = required_tuples(params, constraints, strength)
    not_covered = [t for t in required if t not in covered]
    impossible = [
        t
        for t in not_covered
        if complete(dict(t), params, constraints, set(), strength) is None
    ]
    missing = [t for t in not_covered if t not in impossible]
    feasible = len(required) - len(impossible)
    return {
        "strength": strength,
        "tests": len(tests),
        "feasible_tuples": feasible,
        "covered_tuples": feasible - len(missing),
        "missing": [format_tuple(t) for t in missing],
        "impossible": [format_tuple(t) for t in impossible],
        "problems": problems,
    }


def format_tuple(t: Tuple) -> str:
    return ", ".join(f"{k}={v}" for k, v in t)


def strength_label(strength: int) -> str:
    return {1: "Each-choice (1-wise)", 2: "Pairwise (2-wise)", 3: "3-wise"}.get(
        strength, f"{strength}-wise"
    )


def cartesian_size(params: dict[str, list[str]]) -> int:
    size = 1
    for values in params.values():
        size *= len(values)
    return size


def allowed_size(
    params: dict[str, list[str]],
    constraints: list[dict[str, str]],
    limit: int = 1_000_000,
) -> int | None:
    """Number of full combinations that respect the constraints; None when too many to count."""
    if not constraints:
        return cartesian_size(params)
    if cartesian_size(params) > limit:
        return None
    names = list(params)
    return sum(
        1
        for values in itertools.product(*params.values())
        if not violates(dict(zip(names, values)), constraints)
    )


def coverage_lines(
    params, constraints, tests, required, impossible, strength
) -> list[str]:
    feasible = len(required) - len(impossible)
    allowed = allowed_size(params, constraints)
    total = (
        f"{allowed} allowed combinations"
        if allowed is not None
        else "the full set of allowed combinations"
    )
    if constraints and allowed is not None:
        total += f" ({cartesian_size(params)} before constraints)"
    lines = [
        f"{strength_label(strength)} coverage: {feasible}/{feasible} feasible {strength}-tuples covered (100%).",
        f"Tests: {len(tests)} instead of {total}.",
    ]
    if impossible:
        lines.append(
            "Impossible under the constraints (not counted): "
            + "; ".join(format_tuple(t) for t in impossible)
        )
    return lines


def render_markdown(params, constraints, tests, required, impossible, strength) -> str:
    names = list(params)
    out = [
        "| Test | " + " | ".join(names) + " | Expected result |",
        "|---|" + "---|" * (len(names) + 1),
    ]
    for index, test in enumerate(tests, start=1):
        out.append(
            f"| C{index:02d} | " + " | ".join(test[n] for n in names) + " | (fill in) |"
        )
    return "\n".join(
        out
        + [""]
        + coverage_lines(params, constraints, tests, required, impossible, strength)
    )


def render_matrix(params, constraints, tests, required, impossible, strength) -> str:
    columns = [(n, v) for n, values in params.items() for v in values]
    out = [
        "| Test | " + " | ".join(f"{n}: {v}" for n, v in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for index, test in enumerate(tests, start=1):
        out.append(
            f"| C{index:02d} | "
            + " | ".join("X" if test[n] == v else "" for n, v in columns)
            + " |"
        )
    return "\n".join(
        out
        + [""]
        + coverage_lines(params, constraints, tests, required, impossible, strength)
    )


def render_tree(params) -> str:
    out = ["```mermaid", "graph TD", '    ROOT["Test object"]']
    for p_index, (name, values) in enumerate(params.items(), start=1):
        out.append(f'    ROOT --> P{p_index}["{name}"]')
        for v_index, value in enumerate(values, start=1):
            out.append(f'    P{p_index} --> P{p_index}_{v_index}(("{value}"))')
    out.append("```")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify n-wise test combinations."
    )
    parser.add_argument(
        "model", help="JSON model with parameters and optional constraints"
    )
    parser.add_argument(
        "--strength", type=int, help="interaction strength (default: model value or 2)"
    )
    parser.add_argument(
        "--format", choices=["markdown", "matrix", "tree", "json"], default="markdown"
    )
    parser.add_argument(
        "--verify",
        metavar="TESTS_JSON",
        help="check an existing test set instead of generating one",
    )
    args = parser.parse_args()

    try:
        params, constraints, model_strength = load_model(Path(args.model))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    strength = args.strength or model_strength or 2
    if not 1 <= strength <= len(params):
        print(f"Error: strength must be between 1 and {len(params)}", file=sys.stderr)
        return 2

    if args.verify:
        data = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        tests = data["tests"] if isinstance(data, dict) else data
        report = verify(
            [{str(k): str(v) for k, v in t.items()} for t in tests],
            params,
            constraints,
            strength,
        )
        print(json.dumps(report, indent=2))
        return 0 if not report["missing"] and not report["problems"] else 1

    if args.format == "tree":
        print(render_tree(params))
        return 0

    tests, required, impossible = generate(params, constraints, strength)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "strength": strength,
                    "parameters": params,
                    "constraints": constraints,
                    "tests": tests,
                    "feasible_tuples": len(required) - len(impossible),
                    "impossible": [format_tuple(t) for t in impossible],
                    "cartesian_size": cartesian_size(params),
                    "allowed_size": allowed_size(params, constraints),
                },
                indent=2,
            )
        )
    elif args.format == "matrix":
        print(render_matrix(params, constraints, tests, required, impossible, strength))
    else:
        print(
            render_markdown(params, constraints, tests, required, impossible, strength)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
