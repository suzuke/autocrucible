# Sorting Optimization

You are optimizing a sorting algorithm for 100,000 random integers.

## Goal

Maximize `ops_per_sec` — the number of sort operations completed per second.

## Rules

- Edit only `sort.py`
- Your `sort_array(arr)` function must sort the array **in-place** and return it
- The result must be correctly sorted (benchmark verifies this)
- You may use any algorithm: quicksort, mergesort, radix sort, timsort, hybrid approaches, etc.
- You may use numpy, but the input/output must be a Python list
- Standard library is available (`collections`, `heapq`, `bisect`, etc.)

## Tips

- Python's built-in `list.sort()` is a strong baseline (Timsort in C)
- Beating it in pure Python is very hard — consider algorithmic tricks
- Think about: cache locality, branch prediction, memory allocation
- Hybrid approaches (different algorithms for different sizes) often win
- numpy operations on contiguous arrays can be very fast
