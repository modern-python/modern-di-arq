import modern_di_arq


def test_public_api_importable() -> None:
    assert isinstance(modern_di_arq.__all__, list)
