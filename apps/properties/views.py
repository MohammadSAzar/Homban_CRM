from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.user_serializers import StrictInputSerializer
from .api_services import add_image, get_scoped_file, remove_image, reorder_images, save_file
from .filters import PropertyFileFilter
from .policies import PropertyFilePermission, visible_files
from .serializers import (
    ImageOrderSerializer, ImageWriteSerializer, PropertyFileDetailSerializer,
    PropertyFileImageSerializer, PropertyFileListSerializer, PropertyFileWriteSerializer,
)


def details(actor):
    return visible_files(actor).select_related("workspace", "assigned_to", "city", "region").prefetch_related("images", "valuable_reasons")


class PropertyFilePagination(PageNumberPagination):
    page_size = 50


class PropertyFileViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PropertyFilePermission]
    pagination_class = PropertyFilePagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = PropertyFileFilter
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if self.action == "list":
            return visible_files(self.request.user).select_related("workspace", "assigned_to", "city", "region").order_by("-created_at", "pk")
        return details(self.request.user)

    def get_serializer_class(self):
        return PropertyFileListSerializer if self.action == "list" else PropertyFileDetailSerializer

    def get_object(self):
        queryset = self.get_queryset()
        try:
            return queryset.get(pk=self.kwargs["pk"])
        except queryset.model.DoesNotExist:
            raise NotFound(_("فایل یافت نشد.")) from None

    def create(self, request):
        serializer = PropertyFileWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = save_file(actor=request.user, data=serializer.validated_data)
        return Response(PropertyFileDetailSerializer(details(request.user).get(pk=item.pk)).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        self.get_object()
        serializer = PropertyFileWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        item = save_file(actor=request.user, data=serializer.validated_data, file_id=pk)
        return Response(PropertyFileDetailSerializer(details(request.user).get(pk=item.pk)).data)


class PropertyFileImagesView(APIView):
    permission_classes = [PropertyFilePermission]

    def post(self, request, pk):
        get_scoped_file(request.user, pk)
        serializer = ImageWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image = add_image(actor=request.user, file_id=pk, data=serializer.validated_data)
        return Response(PropertyFileImageSerializer(image).data, status=status.HTTP_201_CREATED)

    def patch(self, request, pk):
        get_scoped_file(request.user, pk)
        serializer = ImageOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reorder_images(actor=request.user, file_id=pk, order=serializer.validated_data["order"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyFileImageView(APIView):
    permission_classes = [PropertyFilePermission]

    def delete(self, request, pk, image_id):
        get_scoped_file(request.user, pk)
        serializer = StrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remove_image(actor=request.user, file_id=pk, image_id=image_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
