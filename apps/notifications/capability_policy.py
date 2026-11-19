"""Source entitlement only; source endpoints retain their own authorization."""
from django.db.models import Q

from apps.organizations.capabilities import resolve_workspace_capability


TEAM_SOURCES = {
    'collaboration_request': 'collaboration',
    'chat_message': 'chat',
    'announcement': 'announcements',
}


def source_allowed(actor, kind, source_id):
    capability = TEAM_SOURCES.get(kind)
    if capability:
        return resolve_workspace_capability(actor.workspace, capability).allowed
    if kind == 'match_recommendation':
        if not resolve_workspace_capability(actor.workspace, 'cross_consultant_matching').allowed:
            from apps.matching.models import MatchRecommendation
            return MatchRecommendation.objects.for_viewer(actor).filter(pk=source_id).exists()
    return True


def entitled_notifications(rows, actor):
    """Bounded SQL filtering, never hydrate source records or private fields."""
    for kind, capability in TEAM_SOURCES.items():
        if not resolve_workspace_capability(actor.workspace, capability).allowed:
            rows = rows.exclude(kind=kind)
    if not resolve_workspace_capability(actor.workspace, 'cross_consultant_matching').allowed:
        from apps.matching.models import MatchRecommendation
        own = MatchRecommendation.objects.for_viewer(actor).values('pk')
        rows = rows.filter(~Q(kind='match_recommendation') | Q(source_id__in=own))
    return rows
