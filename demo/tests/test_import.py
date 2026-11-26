from bwtidy.gateway.app import create_gateway_app

import bwtidy_demo


def test_the_demo_and_the_gateway_it_wraps_import():
    assert bwtidy_demo.__name__ == "bwtidy_demo"
    assert callable(create_gateway_app)
