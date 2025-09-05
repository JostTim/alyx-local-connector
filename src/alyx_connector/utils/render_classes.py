def obfuscate(string: str, max_length: int = 16):
    """Obfuscates a given string by replacing part of it with asterisks.

    Args:
        string (str): The string to be obfuscated. Must be a valid string.
        max_length (int, optional): The maximum length of the obfuscated portion.
            Defaults to 16.

    Raises:
        ValueError: If the input is not a string.

    Returns:
        str: The obfuscated string, where part of the original string is replaced
        with asterisks if its length exceeds 6 characters. If the string length
        is 6 or less, it returns a string of asterisks of the same length.
    """

    if not isinstance(string, str):
        raise ValueError("obfuscated object must be a string")
    if len(string) > 6:
        return string[:4] + ("*" * min(max_length, (len(string) - 4)))
    return "*" * len(string)
