from decimal import Decimal

import pytest
from django.db import connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_canonical_migration_preflight_and_backfill():
    old = ('properties', '0001_initial')
    new = ('properties', '0002_canonical_completeness')
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([old])
        apps = executor.loader.project_state([old, ('accounts', '0003_workspace_scoped_username'), ('organizations', '0003_workspace_region_policy')]).apps
        workspace = apps.get_model('organizations', 'Workspace').objects.create(name='test', slug='canonical', customer_type='consultant')
        user = apps.get_model('accounts', 'User').objects.create(username='canonical', workspace=workspace, role='consultant')
        city = apps.get_model('locations', 'City').objects.create(workspace=workspace, name='city')
        region = apps.get_model('locations', 'Region').objects.create(workspace=workspace, city=city, name='region')
        File = apps.get_model('properties', 'PropertyFile')
        fields = dict(workspace=workspace, assigned_to=user, city=city, region=region, area=100, bedrooms=0, transaction_type='sale', total_price=15277862300)
        valid = File.objects.create(**fields, code='legacy-valid', price_per_square_meter=1)
        bad = File.objects.create(**{**fields, 'region': None}, code='legacy-incomplete')
        with pytest.raises(RuntimeError, match='region_missing'):
            MigrationExecutor(connection).migrate([new])
        valid.refresh_from_db()
        assert valid.price_per_square_meter == 1  # No partial repair or invented Region.
        bad.delete()
        MigrationExecutor(connection).migrate([new])
        valid.refresh_from_db()
        assert valid.price_per_square_meter == Decimal('152000000')
        assert valid.total_price == 15277862300 and valid.area == 100 and valid.region_id == region.pk
        for field in ('region', 'area', 'bedrooms', 'total_price'):
            with pytest.raises(IntegrityError), transaction.atomic():
                File.objects.filter(pk=valid.pk).update(**{field: None})
        MigrationExecutor(connection).migrate([old])
        MigrationExecutor(connection).migrate([new])
        valid.refresh_from_db()
        assert valid.price_per_square_meter == Decimal('152000000')
    finally:
        MigrationExecutor(connection).migrate(latest)
