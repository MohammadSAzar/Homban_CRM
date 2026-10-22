import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_legacy_region_mode_migration_preserves_locations():
    old = ('organizations', '0002_workspace_region_mode')
    new = ('organizations', '0003_workspace_region_policy')
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([old])
        historical = executor.loader.project_state([old, ('locations', '0001_initial')]).apps
        workspace = historical.get_model('organizations', 'Workspace')
        City = historical.get_model('locations', 'City')
        Region = historical.get_model('locations', 'Region')
        ids = {}
        for mode in ('none', 'custom', 'divar'):
            row = workspace.objects.create(name=mode, slug=mode, customer_type='consultant', region_mode=mode)
            ids[mode] = row.pk
        city = City.objects.create(workspace_id=ids['none'], name='تهران')
        region = Region.objects.create(workspace_id=ids['none'], city=city, name='ونک')
        before_city = City.objects.values().get(pk=city.pk)
        before_region = Region.objects.values().get(pk=region.pk)
        executor = MigrationExecutor(connection)
        executor.migrate([new])
        workspace = executor.loader.project_state([new]).apps.get_model('organizations', 'Workspace')
        assert workspace.objects.get(pk=ids['none']).region_mode is None
        for mode in ('custom', 'divar'):
            assert workspace.objects.get(pk=ids[mode]).region_mode == mode
        assert City.objects.values().get(pk=city.pk) == before_city
        assert Region.objects.values().get(pk=region.pk) == before_region
        executor = MigrationExecutor(connection)
        executor.migrate([old])
        workspace = executor.loader.project_state([old]).apps.get_model('organizations', 'Workspace')
        assert workspace.objects.get(pk=ids['none']).region_mode == 'none'
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_product_mode_expand_preserves_unclassified_workspaces():
    old = ('organizations', '0003_workspace_region_policy')
    new = ('organizations', '0004_workspace_mode')
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([old])
        historical = executor.loader.project_state([old, ('locations', '0001_initial')]).apps
        workspace = historical.get_model('organizations', 'Workspace')
        City = historical.get_model('locations', 'City')
        Region = historical.get_model('locations', 'Region')
        before = {}
        for purchaser, region_mode in (
            ('agency_manager', None), ('range_manager', 'custom'), ('consultant', 'divar'),
        ):
            row = workspace.objects.create(
                name=purchaser, slug=purchaser, customer_type=purchaser,
                region_mode=region_mode, is_active=purchaser != 'consultant',
            )
            before[row.pk] = workspace.objects.values().get(pk=row.pk)
        city = City.objects.create(workspace_id=row.pk, name='تهران')
        region = Region.objects.create(workspace_id=row.pk, city=city, name='ونک')
        before_city = City.objects.values().get(pk=city.pk)
        before_region = Region.objects.values().get(pk=region.pk)
        executor = MigrationExecutor(connection)
        executor.migrate([new])
        workspace = executor.loader.project_state([new]).apps.get_model('organizations', 'Workspace')
        for pk, original in before.items():
            migrated = workspace.objects.values().get(pk=pk)
            assert migrated.pop('workspace_mode') is None
            assert migrated == original
        assert City.objects.values().get(pk=city.pk) == before_city
        assert Region.objects.values().get(pk=region.pk) == before_region
    finally:
        MigrationExecutor(connection).migrate(latest)
