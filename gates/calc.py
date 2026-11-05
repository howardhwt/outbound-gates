"""Checked maths: a number the email shows that the gate works out itself.

A calculation is a formula over named inputs, plus the number the email shows,
for example::

    formula: (theirs - ours) / theirs * 100
    inputs:  theirs = 0.006 (a verified source claim), ours = 0.002 (own fact)
    show:    70%

The gate recomputes it from verified inputs every time it checks a draft, and
the shown number passes only if it is within MARGIN of the result. Formulas may
use + - * /, parentheses, the input names, and a few unit constants. Every
input must be used, and at least one must be a sourced claim, so a formula
can't produce a number out of constants and the sender's own facts alone.

Stdlib only. The formula is parsed with ast and evaluated by walking the tree;
nothing is passed to eval().
"""
from __future__ import annotations

import ast
import operator

ALLOWED_CONSTANTS = {60, 24, 7, 30, 365, 100, 1000, 3600}
MARGIN = 0.02  # the shown number may differ from the result by up to 2% of it
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


class CalcError(ValueError):
    """The formula, its inputs or the shown number don't hold up."""


def parse(formula: str) -> ast.expr:
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError:
        raise CalcError(f"formula is not valid arithmetic: {formula!r}") from None
    return tree.body


def names_and_constants(node: ast.expr) -> tuple[set[str], set[float]]:
    """The names and constants a formula uses. Anything else is refused."""
    names: set[str] = set()
    constants: set[float] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Constant):
            if isinstance(sub.value, bool) or not isinstance(sub.value, (int, float)):
                raise CalcError(f"only numbers may appear as constants, not {sub.value!r}")
            constants.add(sub.value)
        elif isinstance(sub, (ast.BinOp, ast.UnaryOp)):
            op = sub.op
            if isinstance(sub, ast.BinOp) and type(op) not in _OPS:
                raise CalcError(f"only + - * / are allowed, not {type(op).__name__}")
            if isinstance(sub, ast.UnaryOp) and not isinstance(op, ast.USub):
                raise CalcError(f"only unary minus is allowed, not {type(op).__name__}")
        elif not isinstance(sub, (ast.Load, ast.operator, ast.unaryop)):
            raise CalcError(f"{type(sub).__name__} is not allowed in a formula (only arithmetic)")
    return names, constants


def _evaluate(node: ast.expr, values: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        return -_evaluate(node.operand, values)
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left, values), _evaluate(node.right, values)
        if isinstance(node.op, ast.Div) and right == 0:
            raise CalcError("the formula divides by zero")
        return _OPS[type(node.op)](left, right)
    raise CalcError(f"{type(node).__name__} is not allowed")  # unreachable after validate


def shown_value(token: str) -> float:
    """'70%' -> 70.0, '$1,200' -> 1200.0, '3' -> 3.0."""
    cleaned = token.strip().replace(",", "").replace("$", "").rstrip("%").rstrip(".")
    try:
        return float(cleaned)
    except ValueError:
        raise CalcError(f"the shown number {token!r} is not a number") from None


def within_margin(shown: float, result: float, margin: float = MARGIN) -> bool:
    if result == 0:
        return shown == 0
    return abs(shown - result) <= margin * abs(result)


def check(formula: str, inputs: dict[str, dict], show: str) -> float:
    """Validate and compute. inputs: name -> {"value": "0.006", "source": "claim"|"own"}.

    Returns the result. Raises CalcError with the reason when anything fails.
    """
    node = parse(formula)
    names, constants = names_and_constants(node)
    bad_constants = sorted(c for c in constants if c not in ALLOWED_CONSTANTS)
    if bad_constants:
        allowed = ", ".join(str(c) for c in sorted(ALLOWED_CONSTANTS))
        raise CalcError(f"constants {bad_constants} aren't allowed; only unit constants ({allowed})")
    unknown = sorted(names - set(inputs))
    if unknown:
        raise CalcError(f"the formula uses names with no input: {', '.join(unknown)}")
    unused = sorted(set(inputs) - names)
    if unused:
        raise CalcError(f"every input must be used; unused: {', '.join(unused)}")
    if not any(i.get("source") == "claim" for i in inputs.values()):
        raise CalcError("at least one input must be a sourced claim, not only your own facts")
    values = {}
    for name, item in inputs.items():
        try:
            values[name] = shown_value(str(item.get("value", "")))
        except CalcError:
            raise CalcError(f"input {name} is not a number: {item.get('value')!r}") from None
    result = _evaluate(node, values)
    shown = shown_value(show)
    if not within_margin(shown, result):
        raise CalcError(f"the formula gives {result:.6g}, but the email shows {show!r} "
                        f"(more than {MARGIN:.0%} apart)")
    return result
