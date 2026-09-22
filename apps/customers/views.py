from django.db.models import Prefetch
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.locations.models import Region
from .api_services import get_scoped_customer, save_customer
from .filters import CustomerFilter
from .policies import CustomerPermission, visible_customers
from .serializers import CustomerDetailSerializer, CustomerListSerializer, CustomerWriteSerializer


def details(actor):
    regions = Region.objects.filter(workspace_id=actor.workspace_id, city__workspace_id=actor.workspace_id).order_by("name", "pk")
    return visible_customers(actor).select_related("workspace", "assigned_to").prefetch_related(
        Prefetch("preferred_regions", queryset=regions, to_attr="scoped_regions"), "valuable_reasons",
    )


class CustomerPagination(PageNumberPagination):
    page_size = 50


class CustomerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [CustomerPermission]
    pagination_class = CustomerPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = CustomerFilter
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if self.action != "list":
            return details(self.request.user)
        return visible_customers(self.request.user).select_related("workspace", "assigned_to").order_by("-created_at", "pk")

    def get_serializer_class(self):
        return CustomerListSerializer if self.action == "list" else CustomerDetailSerializer

    def get_object(self):
        queryset = self.get_queryset()
        try:
            return queryset.get(pk=self.kwargs["pk"])
        except queryset.model.DoesNotExist:
            raise NotFound(_("مشتری یافت نشد.")) from None

    def create(self, request):
        serializer = CustomerWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = save_customer(actor=request.user, data=serializer.validated_data)
        return Response(CustomerDetailSerializer(details(request.user).get(pk=item.pk)).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        get_scoped_customer(request.user, pk)
        serializer = CustomerWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        item = save_customer(actor=request.user, data=serializer.validated_data, customer_id=pk)
        return Response(CustomerDetailSerializer(details(request.user).get(pk=item.pk)).data)
