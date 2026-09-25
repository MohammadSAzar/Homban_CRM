import pytest
from django.db import connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_canonical_migration_refuses_incomplete_data_and_preserves_explicit_regions():
    old = ("customers", "0001_initial")
    new = ("customers", "0002_canonical_completeness")
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([old])
        apps = executor.loader.project_state([old, ("accounts", "0003_workspace_scoped_username"), ("organizations", "0003_workspace_region_policy")]).apps
        workspace = apps.get_model("organizations", "Workspace").objects.create(name="test", slug="customer-canonical", customer_type="consultant")
        user = apps.get_model("accounts", "User").objects.create(username="test", workspace=workspace, role="consultant")
        city = apps.get_model("locations", "City").objects.create(workspace=workspace, name="city")
        region = apps.get_model("locations", "Region").objects.create(workspace=workspace, city=city, name="region")
        Customer = apps.get_model("customers", "Customer")
        Preference = apps.get_model("customers", "CustomerRegionPreference")
        fields = dict(workspace=workspace, assigned_to=user, customer_type="buyer", name="test", min_area=0, max_area=100, bedrooms=0, budget=0)
        valid = Customer.objects.create(**fields, code="valid")
        Preference.objects.create(customer=valid, region=region)
        bad = Customer.objects.create(workspace=workspace, assigned_to=user, customer_type="buyer", name="incomplete", code="bad")
        with pytest.raises(RuntimeError, match="min_area_missing"):
            MigrationExecutor(connection).migrate([new])
        bad.delete()
        empty = Customer.objects.create(**fields, code="empty-geography")
        with pytest.raises(RuntimeError, match="zero_preferences"):
            MigrationExecutor(connection).migrate([new])
        empty.delete()
        MigrationExecutor(connection).migrate([new])
        current = MigrationExecutor(connection).loader.project_state([new]).apps.get_model("customers", "Customer")
        migrated = current.objects.get(pk=valid.pk)
        assert not migrated.all_regions and migrated.budget == 0 and migrated.budget_status is None
        assert list(migrated.preferred_regions.values_list("pk", flat=True)) == [region.pk]
        for field in ("min_area", "max_area", "bedrooms", "budget"):
            with pytest.raises(IntegrityError), transaction.atomic():
                current.objects.filter(pk=valid.pk).update(**{field: None})
        MigrationExecutor(connection).migrate([old])
        MigrationExecutor(connection).migrate([new])
        assert not current.objects.get(pk=valid.pk).all_regions
    finally:
        MigrationExecutor(connection).migrate(latest)
