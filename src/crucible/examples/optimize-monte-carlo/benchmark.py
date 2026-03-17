from estimate import estimate

TRUE_VALUE = 1 / 3

result = estimate()
error = abs(result - TRUE_VALUE)
print(f"error: {error:.6f}")
