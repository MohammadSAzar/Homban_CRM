"""Real independent MySQL connections; synchronized overlapping conversion attempts."""
import pytest
from unittest.mock import patch
from apps.organizations.tests.test_topology_concurrency import overlap
from apps.staging.conversion import convert_staged_record
from apps.properties.models import PropertyFile
from .test_conversion import world, staged

pytestmark = pytest.mark.django_db(transaction=True)


def test_simultaneous_conversion_creates_exactly_one_target(world):
    row = staged(world)
    with patch("apps.matching.generation_events.publish_work"):
        outcomes = overlap(
            lambda: convert_staged_record(record_id=row.pk, actor=world.actor),
            lambda: convert_staged_record(record_id=row.pk, actor=world.actor),
        )
    assert [ok for ok, _ in outcomes] == [True, False]
    row.refresh_from_db()
    assert row.status == "converted"
    assert PropertyFile.objects.count() == 1
    assert row.converted_property_file_id == PropertyFile.objects.get().pk
