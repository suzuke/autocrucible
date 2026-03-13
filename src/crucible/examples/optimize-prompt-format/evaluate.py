"""Evaluation harness for prompt.txt — DO NOT MODIFY.

Tests the system prompt on 10 format conversion tasks.
Uses exact string match scoring (after stripping whitespace).

Requires 'claude' CLI to be installed and authenticated.

Output format (parsed by crucible):
    accuracy: <float>   (0.0 to 1.0)
    correct: <int>
    total: <int>
"""

import subprocess
import sys
import re
import traceback

TEST_PROBLEMS = [
    ('Convert date to ISO format (YYYY-MM-DD): "15 April 2024"',
     "2024-04-15"),
    ('Convert date to ISO format (YYYY-MM-DD): "March 3, 2025"',
     "2025-03-03"),
    ('Convert to centimeters, rounded to 2 decimal places: "5 feet 11 inches"',
     "180.34 cm"),
    ('Convert to centimeters, rounded to 2 decimal places: "6 feet 2 inches"',
     "187.96 cm"),
    ('Convert Fahrenheit to Celsius, rounded to 2 decimal places: "32 degrees Fahrenheit"',
     "0.00 degrees Celsius"),
    ('Convert Fahrenheit to Celsius, rounded to 2 decimal places: "212 degrees Fahrenheit"',
     "100.00 degrees Celsius"),
    ('Normalize Taiwan landline to international format: "(02) 1234-5678"',
     "+886-2-1234-5678"),
    ('Normalize Taiwan mobile to international format: "0912-345-678"',
     "+886-912-345-678"),
    ('Remove thousands separators and currency symbol, keep 2 decimal places: "$1,234.50"',
     "1234.50"),
    ('Remove thousands separators and currency symbol, keep 2 decimal places: "$98,765.00"',
     "98765.00"),
]

MAX_PROMPT_LENGTH = 2000


def answers_match(predicted: str | None, expected: str) -> bool:
    if predicted is None:
        return False
    return predicted.strip() == expected.strip()


def main():
    try:
        with open("prompt.txt", "r") as f:
            system_prompt = f.read().strip()

        if len(system_prompt) > MAX_PROMPT_LENGTH:
            print(f"VIOLATION: prompt too long ({len(system_prompt)} > {MAX_PROMPT_LENGTH} chars)")
            print("accuracy: 0.0")
            return

        correct = 0
        total = len(TEST_PROBLEMS)

        # Send all questions in one batch for efficiency
        questions_text = "\n\n".join(
            f"Q{i+1}: {q}" for i, (q, _) in enumerate(TEST_PROBLEMS)
        )
        user_message = (
            questions_text
            + "\n\nOutput ONLY the converted result for each question, one per line. "
            + "Format: Q1: <result>\nQ2: <result>\n..."
        )

        result = subprocess.run(
            ["claude", "-p", "--system-prompt", system_prompt, user_message],
            capture_output=True,
            text=True,
            timeout=80,
        )

        if result.returncode != 0:
            stderr = result.stderr[:300] if result.stderr else "(no stderr)"
            print(f"ERROR: claude CLI failed (exit {result.returncode}): {stderr}")
            print("accuracy: 0.0")
            return

        output = result.stdout

        for i, (_, expected) in enumerate(TEST_PROBLEMS):
            match = re.search(rf"Q{i+1}:\s*(.+?)(?=\n|$)", output)
            if match:
                predicted = match.group(1).strip()
            else:
                predicted = None
            if answers_match(predicted, expected):
                correct += 1

        accuracy = correct / total
        print(f"accuracy: {accuracy:.2f}")
        print(f"correct: {correct}")
        print(f"total: {total}")

    except subprocess.TimeoutExpired:
        print("ERROR: claude CLI timed out after 80s")
        print("accuracy: 0.0")
    except FileNotFoundError:
        print("ERROR: 'claude' CLI not found — ensure Claude Code is installed")
        print("accuracy: 0.0")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("accuracy: 0.0")


if __name__ == "__main__":
    main()
