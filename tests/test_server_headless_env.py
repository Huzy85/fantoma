import importlib.util
import os


def _load_server():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py")
    spec = importlib.util.spec_from_file_location("fantoma_server_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_headless_env_text_becomes_real_value():
    srv = _load_server()
    assert srv._headless_from_env("true") is True
    assert srv._headless_from_env("1") is True
    assert srv._headless_from_env("False") is False
    assert srv._headless_from_env("virtual") == "virtual"
    assert srv._headless_from_env("") == "virtual"
