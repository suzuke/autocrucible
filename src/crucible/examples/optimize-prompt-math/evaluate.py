"""Evaluation harness for prompt.txt — DO NOT MODIFY.

Sends 10 math word problems to Claude using the system prompt from prompt.txt.
Evaluates answers against ground truth numeric values.

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
    ("A store offers 20% discount. An item originally costs $45. What is the sale price in dollars?",
     "36"),
    ("A train travels 240 kilometers in 3 hours. What is its speed in kilometers per hour?",
     "80"),
    ("Sarah has 3 times as many apples as Tom. Together they have 48 apples. How many apples does Tom have?",
     "12"),
    ("A recipe needs 2.5 cups of flour to make 12 cookies. How many cups of flour are needed for 30 cookies?",
     "6.25"),
    ("A water tank is 60% full and contains 180 liters. What is the full capacity of the tank in liters?",
     "300"),
    ("If 15% of a number is 45, what is the number?",
     "300"),
    ("A car travels 150 km at 60 km/h, then 100 km at 50 km/h. What is the total travel time in hours?",
     "4.5"),
    ("The sum of two consecutive even numbers is 46. What is the larger number?",
     "24"),
    ("A rectangular garden is 8 meters wide and 12 meters long. How many meters of fencing are needed to enclose it?",
     "40"),
    ("A worker earns $18 per hour and works 7.5 hours. How much does the worker earn in total dollars?",
     "135"),
]

MAX_PROMPT_LENGTH = 2000


def normalize_answer(text: str) -> str | None:
    text = text.replace("$", "").replace(",", "").strip()
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    if numbers:
        return numbers[-1]
    return None


def answers_match(predicted: str | None, expected: str) -> bool:
    if predicted is None:
        return False
    try:
        pred_val = float(predicted)
        exp_val = float(expected)
        return abs(pred_val - exp_val) < 0.01
    except ValueError:
        return predicted.strip() == expected.strip()


def main():
    try:
        with open("prompt.txt", "r") as f:
            system_prompt = f.read().strip()

        if len(system_prompt) > MAX_PROMPT_LENGTH:
            print(f"VIOLATION: prompt too long ({len(system_prompt)} > {MAX_PROMPT_LENGTH} chars)")
            print("accuracy: 0.0")
            return

        questions_text = "\n\n".join(
            f"Q{i+1}: {q}" for i, (q, _) in enumerate(TEST_PROBLEMS)
        )
        user_message = (
            questions_text
            + "\n\nAnswer each question with just the number. "
            + "Format: Q1: <number> Q2: <number> Q3: <number> Q4: <number> Q5: <number> "
            + "Q6: <number> Q7: <number> Q8: <number> Q9: <number> Q10: <number>"
        )

        result = subprocess.run(
            ["claude", "-p", "--system-prompt", system_prompt, user_message],
            capture_output=True,
            text=True,
            timeout=50,
        )

        if result.returncode != 0:
            stderr = result.stderr[:300] if result.stderr else "(no stderr)"
            print(f"ERROR: claude CLI failed (exit {result.returncode}): {stderr}")
            print("accuracy: 0.0")
            return

        output = result.stdout

        correct = 0
        total = len(TEST_PROBLEMS)
        for i, (_, expected) in enumerate(TEST_PROBLEMS):
            match = re.search(rf"Q{i+1}:\s*([^\n]+)", output)
            if match:
                predicted = normalize_answer(match.group(1))
            else:
                predicted = None
            if answers_match(predicted, expected):
                correct += 1

        accuracy = correct / total
        print(f"accuracy: {accuracy:.2f}")
        print(f"correct: {correct}")
        print(f"total: {total}")

    except subprocess.TimeoutExpired:
        print("ERROR: claude CLI timed out after 50s")
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
