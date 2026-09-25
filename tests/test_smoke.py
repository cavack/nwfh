from nwfh.main import main


def test_import_and_run() -> None:
    assert callable(main)
