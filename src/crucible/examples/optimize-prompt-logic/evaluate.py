"""Evaluation harness for prompt.txt — DO NOT MODIFY.

Sends 10 logic reasoning problems to Claude using the system prompt from prompt.txt.
Evaluates answers against ground truth labels (True/False/Cannot determine).

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
    ("All cats are mammals. Whiskers is a cat. Is Whiskers a mammal?",
     "True"),
    ("No fish can breathe air. Nemo is a fish. Can Nemo breathe air?",
     "False"),
    ("Some students are athletes. Some athletes are musicians. Are some students musicians?",
     "Cannot determine"),
    ("If it rains, the ground gets wet. The ground is wet. Did it rain?",
     "Cannot determine"),
    ("All squares are rectangles. All rectangles have four sides. Do all squares have four sides?",
     "True"),
    ("Every dog is an animal. Rex is not an animal. Is Rex a dog?",
     "False"),
    ("If A implies B, and B implies C, and A is true, is C true?",
     "True"),
    ("All members of Club X speak French. Marie speaks French. Is Marie a member of Club X?",
     "Cannot determine"),
    ("If today is Monday, the shop is closed. The shop is open. Is today Monday?",
     "False"),
    ("Some fruits are sweet. All apples are fruits. Are all apples sweet?",
     "Cannot determine"),
]

MAX_PROMPT_LENGTH = 2000


def normalize_answer(text: str) -> str | None:
    text = text.strip().lower()
    if "cannot" in text or "can't" in text or "undetermined" in text or "uncertain" in text or "not enough" in text or "doesn't" in text:
        return "Cannot determine"
    if "true" in text or "yes" in text:
        return "True"
    if "false" in text or "no" in text:
        return "False"
    return None


def answers_match(predicted: str | None, expected: str) -> bool:
    if predicted is None:
        return False
    return predicted.lower() == expected.lower()


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
            + "\n\nAnswer each with exactly one of: True, False, or Cannot determine. "
            + "Format: Q1: <answer> Q2: <answer> Q3: <answer> Q4: <answer> Q5: <answer> "
            + "Q6: <answer> Q7: <answer> Q8: <answer> Q9: <answer> Q10: <answer>"
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
                raw = match.group(1).strip()
                predicted = normalize_answer(raw)
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
