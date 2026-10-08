import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def isolated_chat_channel_layer(settings):
    # Tests never require or publish private events to external Redis.
    settings.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


@pytest.fixture(autouse=True)
def isolated_matching_broker():
    # Unit/DB suites must never publish to a real broker; tasks are invoked explicitly.
    with patch("apps.matching.tasks.generate_recommendations.delay") as publish:
        yield publish
