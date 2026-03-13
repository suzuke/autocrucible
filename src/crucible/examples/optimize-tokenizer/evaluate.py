"""Evaluation harness for tokenizer.py — DO NOT MODIFY.

Measures average tokens per character on a held-out news corpus.
Lower tokens_per_char = better compression.

Output format (parsed by crucible):
    tokens_per_char: <float>
    num_merges: <int>
    num_tokens: <int>
"""

import sys
import traceback

# Held-out test corpus: news style, different from training corpus.txt
TEST_CORPUS = """\
Scientists announced today the discovery of a new species of deep-sea fish \
near the Mariana Trench. The creature, which glows blue in complete darkness, \
was captured on video for the first time by a remotely operated vehicle. \
Researchers from the National Oceanic and Atmospheric Administration said \
the fish exhibits unusual feeding behavior not previously observed in related species.

The city council approved a new budget plan that allocates funding for road \
repairs and public transportation upgrades. The measure passed with a seven to \
two vote after months of debate over how to balance infrastructure spending with \
education priorities. Officials estimate the improvements will benefit approximately \
two hundred thousand daily commuters across the metropolitan area.

Global temperatures continued to break records this year according to data \
released by climate scientists. The report shows that average surface temperatures \
have risen by nearly one and a half degrees Celsius compared to pre-industrial \
levels. Experts warn that without significant reductions in greenhouse gas emissions, \
the frequency of extreme weather events will continue to increase throughout this decade.

Shares in technology companies fell sharply after the central bank announced \
an unexpected interest rate increase. The benchmark index dropped three percent \
in early trading before recovering some losses by afternoon. Analysts suggest \
investors are reassessing growth projections for software and semiconductor firms \
in light of higher borrowing costs and tightening credit conditions.

Health authorities confirmed the effectiveness of a new vaccine against a \
respiratory illness that affected millions last winter. Clinical trials involving \
forty thousand participants showed ninety two percent protection against severe disease. \
The vaccine is expected to receive full regulatory approval within the next six weeks \
and will be distributed through existing pharmacy networks.\
"""


def main():
    try:
        from tokenizer import build_merges, tokenize

        with open("corpus.txt", "r") as f:
            train_corpus = f.read()

        merges = build_merges(train_corpus)

        if not isinstance(merges, list):
            print("ERROR: build_merges must return a list")
            print("tokens_per_char: 999.0")
            sys.exit(0)

        if len(merges) > 500:
            print(f"VIOLATION: too many merge rules ({len(merges)} > 500), truncating to 500")
            merges = merges[:500]

        for i, rule in enumerate(merges):
            if not (isinstance(rule, tuple) and len(rule) == 2 and
                    isinstance(rule[0], str) and isinstance(rule[1], str)):
                print(f"ERROR: merge rule {i} must be tuple[str, str], got {type(rule)}")
                print("tokens_per_char: 999.0")
                sys.exit(0)

        tokens = tokenize(TEST_CORPUS, merges)

        if not isinstance(tokens, list):
            print("ERROR: tokenize must return a list")
            print("tokens_per_char: 999.0")
            sys.exit(0)

        reconstructed = "".join(tokens)
        if reconstructed != TEST_CORPUS:
            print("ERROR: tokenize is lossy — joining tokens does not reconstruct original text")
            print(f"  expected length: {len(TEST_CORPUS)}, got: {len(reconstructed)}")
            print("tokens_per_char: 999.0")
            sys.exit(0)

        tokens_per_char = len(tokens) / len(TEST_CORPUS)
        print(f"tokens_per_char: {tokens_per_char:.4f}")
        print(f"num_merges: {len(merges)}")
        print(f"num_tokens: {len(tokens)}")
        print(f"num_chars: {len(TEST_CORPUS)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("tokens_per_char: 999.0")


if __name__ == "__main__":
    main()
