def encrypt(text: str, key: dict) -> str:
    """Encrypt text using a substitution cipher key mapping."""
    result = []
    for char in text:
        result.append(key.get(char, char))
    return "".join(result)
