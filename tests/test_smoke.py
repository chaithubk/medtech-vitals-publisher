"""Smoke test to ensure CI passes with stub code."""


def test_import():
    """Test that we can import the simulator module."""
    import src.simulator

    assert src.simulator is not None


def test_main_callable():
    """Test that main function exists."""
    from src.simulator import main

    assert callable(main)
