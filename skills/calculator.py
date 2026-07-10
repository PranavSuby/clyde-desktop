"""Exact math via sympy — no LLM arithmetic, ever.

Input is validated with Python's ast module before any sympy parsing:
only whitelisted node types and function names are allowed, so attribute
chains (`().__class__...`), subscripts, and unknown calls are rejected.
sympy's parser uses eval under the hood and must never see raw input.
"""

import ast

SKILL = {
    "name": "calculator",
    "description": (
        "Evaluate a math expression EXACTLY. Use this for ALL arithmetic, "
        "algebra, and numeric questions instead of computing yourself: "
        "e.g. '2**10 + 5', 'sqrt(2)*pi', 'solve(x**2 - 4, x)', "
        "'integrate(sin(x), x)', 'diff(x**3, x)'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The expression, in Python/sympy syntax. "
                               "Use ** for powers (^ is also accepted).",
            },
        },
        "required": ["expression"],
    },
    "timeout": 5,
}

_ALLOWED_FUNCS = {
    "solve", "integrate", "diff", "limit", "factor", "expand", "simplify",
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "exp",
    "Abs", "abs", "floor", "ceiling", "gcd", "lcm", "factorial", "Rational",
}
_ALLOWED_NAMES = _ALLOWED_FUNCS | {"x", "y", "z", "pi", "E", "I", "oo"}
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Constant,
    ast.Tuple, ast.List, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.BitXor, ast.Load,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def _validate(expr: str) -> str | None:
    """Return an error message, or None if the expression is safe."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return f"could not parse: {e.msg}"
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return f"'{type(node).__name__}' is not allowed"
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) \
                    or node.func.id not in _ALLOWED_FUNCS:
                return "only these functions are allowed: " \
                       + ", ".join(sorted(_ALLOWED_FUNCS))
            if node.keywords:
                return "keyword arguments are not allowed"
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            return f"unknown name '{node.id}'"
        if isinstance(node, ast.Constant) \
                and not isinstance(node.value, (int, float)):
            return "only numeric constants are allowed"
    return None


def run(args: dict) -> str:
    import sympy

    expr_str = str(args.get("expression", "")).strip().replace("^", "**")
    if not expr_str:
        return "Error: empty expression"
    if len(expr_str) > 300:
        return "Error: expression too long"
    problem = _validate(expr_str)
    if problem:
        return f"Error: {problem}"

    env = {name: getattr(sympy, name) for name in _ALLOWED_FUNCS
           if hasattr(sympy, name)}
    env["abs"] = sympy.Abs
    env["pi"], env["E"], env["I"], env["oo"] = sympy.pi, sympy.E, sympy.I, sympy.oo
    env["x"], env["y"], env["z"] = sympy.symbols("x y z")
    try:
        result = eval(  # noqa: S307 — input AST-validated above
            compile(ast.parse(expr_str, mode="eval"), "<calc>", "eval"),
            {"__builtins__": {}}, env,
        )
    except Exception as e:
        return f"Error: {e}"

    try:
        simplified = sympy.simplify(result) if hasattr(result, "free_symbols") else result
        numeric = ""
        if hasattr(simplified, "free_symbols") and not simplified.free_symbols \
                and not getattr(simplified, "is_Integer", False):
            approx = sympy.N(simplified, 12)
            if str(approx) != str(simplified):
                numeric = f" ≈ {approx}"
        return f"{simplified}{numeric}"
    except Exception:
        return str(result)
