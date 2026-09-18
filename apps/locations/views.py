from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .policies import LocationPermission, visible_cities, visible_regions
from .serializers import (
    CityFilterSerializer, CityReadSerializer, CityWriteSerializer,
    RegionFilterSerializer, RegionReadSerializer, RegionWriteSerializer,
    RegionModeReadSerializer, RegionModeWriteSerializer,
)
from .services import save_city, save_region, set_region_mode


class LocationPagination(PageNumberPagination):
    page_size = 50


class LocationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [LocationPermission]
    pagination_class = LocationPagination
    http_method_names = ["get", "post", "patch", "head", "options"]
    filter_backends = []

    def get_queryset(self):
        queryset = self.visible_queryset(self.request.user)
        if self.action == "list":
            # Query parameters are optional filters, not HTML checkbox inputs.
            filters = self.filter_serializer(data=self.request.query_params.dict())
            filters.is_valid(raise_exception=True)
            queryset = queryset.filter(**filters.validated_data)
        return queryset

    def get_object(self):
        queryset = self.get_queryset()
        try:
            return queryset.get(pk=self.kwargs["pk"])
        except queryset.model.DoesNotExist:
            raise NotFound(_("مکان یافت نشد.")) from None

    def create(self, request):
        serializer = self.write_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = self.save_location(actor=request.user, data=serializer.validated_data)
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        target = self.get_object()
        serializer = self.write_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = self.save_location(actor=request.user, data=serializer.validated_data, object_id=target.pk)
        return Response(self.get_serializer(instance).data)


class CityViewSet(LocationViewSet):
    serializer_class = CityReadSerializer
    write_serializer = CityWriteSerializer
    filter_serializer = CityFilterSerializer
    visible_queryset = staticmethod(visible_cities)
    save_location = staticmethod(save_city)


class RegionViewSet(LocationViewSet):
    serializer_class = RegionReadSerializer
    write_serializer = RegionWriteSerializer
    filter_serializer = RegionFilterSerializer
    visible_queryset = staticmethod(visible_regions)
    save_location = staticmethod(save_region)


class LocationSettingsView(APIView):
    permission_classes = [LocationPermission]

    def get(self, request):
        return Response(RegionModeReadSerializer(request.user.workspace).data)

    def patch(self, request):
        serializer = RegionModeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = set_region_mode(actor=request.user, data=serializer.validated_data)
        return Response(RegionModeReadSerializer(workspace).data)
