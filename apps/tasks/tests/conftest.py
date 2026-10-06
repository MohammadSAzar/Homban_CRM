from types import SimpleNamespace
import pytest
from apps.accounts.models import User
from apps.organizations.models import Workspace


@pytest.fixture
def people():
    workspace = Workspace.objects.create(name='مجموعه',slug='task-first',customer_type='agency_manager')
    foreign_workspace = Workspace.objects.create(name='دیگر',slug='task-other',customer_type='consultant')
    def user(name, role='consultant', ws=workspace):
        return User.objects.create_user(username=name,workspace=ws,role=role)
    owner, other, foreign = user('owner'), user('other'), user('foreign',ws=foreign_workspace)
    return SimpleNamespace(**locals())
