import pytest
from django.core.exceptions import ValidationError

from apps.locations.models import City, Region
from apps.organizations.models import Workspace


@pytest.mark.django_db
def test_region_cannot_use_city_from_another_workspace():
    workspace_1 = Workspace.objects.create(
        name="املاک اول",
        slug="agency-1",
        customer_type="agency_manager",
    )

    workspace_2 = Workspace.objects.create(
        name="املاک دوم",
        slug="agency-2",
        customer_type="agency_manager",
    )

    city = City.objects.create(
        workspace=workspace_1,
        name="تهران",
    )

    region = Region(
        workspace=workspace_2,
        city=city,
        name="میرداماد",
    )

    with pytest.raises(ValidationError):
        region.full_clean()

