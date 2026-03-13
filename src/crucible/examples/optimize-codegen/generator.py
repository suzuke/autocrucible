"""Code generator — edit this file to improve score.

Interface:
  generate(spec: dict) -> str
      Given a task specification, return a Python code string.
      The code must assign its answer to a variable named `result`.

Example:
  generate({"task": "sum_of_squares", "n": 100})
  # Should return something like:
  # "n = 100\nresult = sum(i*i for i in range(1, n+1))"

The generated code is executed and timed.
Score = mean(correctness × speed_ratio) across 10 tasks.
Faster than reference naive implementation = higher speed_ratio (capped at 10x).

See spec_schema.txt for the spec format and 2 example tasks.
"""


def generate(spec: dict) -> str:
    """Generate Python code for the given task spec."""
    task = spec.get("task", "")

    if task == "sum_of_squares":
        n = spec["n"]
        return f"result = sum(i*i for i in range(1, {n}+1))"

    elif task == "count_vowels":
        text = repr(spec["text"])
        return f"result = sum(1 for c in {text}.lower() if c in 'aeiou')"

    else:
        # Unknown task: return 0
        return "result = 0"
