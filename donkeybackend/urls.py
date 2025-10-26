from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from donkeybackend.api import api, combined_openapi_schema

urlpatterns = [
    # DRF schema and docs moved under /api/drf/* to avoid conflict with Ninja docs at /api/docs
    path("api/drf/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/drf/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # Combined DRF + Ninja schema and docs
    path("api/schema/combined/", combined_openapi_schema, name="combined-schema"),
    path("api/docs/combined/", SpectacularSwaggerView.as_view(url_name="combined-schema"), name="swagger-ui-combined"),

    # DRF app endpoints
    path("api/accounts/", include("accounts.urls")),

    # Ninja API (includes /api/docs and /api/schedule/*)
    path("api/", api.urls),
]
