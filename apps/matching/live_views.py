from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView

from .live_serializers import LivePageSerializer
from .live_services import live_matches


class LivePagination(PageNumberPagination):
    page_size = 50


class LiveMatchesView(APIView):
    direction = None
    weak_preview = False

    def post(self, request, pk):
        page = LivePageSerializer(data=request.query_params.dict())
        page.is_valid(raise_exception=True)
        matches = live_matches(actor=request.user, source_id=pk, direction=self.direction, data=request.data, weak_preview=self.weak_preview)
        pagination = LivePagination()
        results = pagination.paginate_queryset(matches, request, view=self)
        return pagination.get_paginated_response(results)


class CustomerMatchesView(LiveMatchesView):
    direction = "customer"


class PropertyFileMatchesView(LiveMatchesView):
    direction = "property_file"


class WeakCustomerMatchesView(CustomerMatchesView):
    weak_preview = True


class WeakPropertyFileMatchesView(PropertyFileMatchesView):
    weak_preview = True
