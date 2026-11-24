from uvicorn.config import LOGGING_CONFIG

from bwtidy.logs import uvicorn_log_config


def test_uvicorn_lines_start_with_the_time():
    config = uvicorn_log_config()
    for name in ("default", "access"):
        assert config["formatters"][name]["fmt"].startswith("%(asctime)s ")


def test_uvicorn_keeps_its_own_config():
    uvicorn_log_config()
    assert "asctime" not in LOGGING_CONFIG["formatters"]["default"]["fmt"]
