import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.locations.models import City, Region


@pytest.mark.django_db(transaction=True)
def test_legacy_region_mode_migration_preserves_locations():
    old = ('organizations', '0002_workspace_region_mode')
    new = ('organizations', '0003_workspace_region_policy')
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([old])
        workspace = executor.loader.project_state([old]).apps.get_model('organizations', 'Workspace')
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
