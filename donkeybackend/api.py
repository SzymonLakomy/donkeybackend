from ninja import NinjaAPI
from django.http import JsonResponse
from drf_spectacular.generators import SchemaGenerator
from schedule.api import api as schedule_api
from calendars.api import api as calendars_api

# Main Ninja API instance for the project
api = NinjaAPI(
    title="Donkey Ninja API",
    version="1.0.0",
    docs_url="/docs",
    csrf=False,
)

# Mount routers from apps
api.add_router("/schedule", schedule_api)
api.add_router("/calendar", calendars_api)


def _get_drf_schema_dict() -> dict:
    gen = SchemaGenerator()
    schema = gen.get_schema(request=None, public=True)
    # drf-spectacular returns an object with to_dict()
    if hasattr(schema, "to_dict"):
        return schema.to_dict()
    return schema or {}


def _get_ninja_schema_dict() -> dict:
    return api.get_openapi_schema() or {}


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            if k not in dst:
                dst[k] = v
    return dst


def combined_openapi_schema(request):
    drf_schema = _get_drf_schema_dict()
    ninja_schema = _get_ninja_schema_dict()

    combined = dict(drf_schema) if isinstance(drf_schema, dict) else {}

    # Merge info/title
    combined.setdefault("openapi", ninja_schema.get("openapi", "3.0.3"))
    combined.setdefault("info", {
        "title": "Combined API",
        "version": "1.0.0",
        "description": "Połączona specyfikacja DRF + Ninja"
    })

    # Merge paths (prefix Ninja paths with /api)
    combined_paths = dict(combined.get("paths", {}))
    ninja_paths = ninja_schema.get("paths", {}) or {}
    for path, spec in ninja_paths.items():
        full_path = path if path.startswith("/api") else f"/api{path}"
        if full_path not in combined_paths:
            combined_paths[full_path] = spec
    combined["paths"] = combined_paths

    # Merge components (schemas/securitySchemes/parameters, etc.)
    combined_components = dict(combined.get("components", {}) or {})
    ninja_components = ninja_schema.get("components", {}) or {}
    combined["components"] = _deep_merge(combined_components, ninja_components)

    # Merge tags
    existing_tags = {t.get("name"): t for t in (combined.get("tags", []) or []) if isinstance(t, dict) and t.get("name")}
    for t in ninja_schema.get("tags", []) or []:
        name = t.get("name") if isinstance(t, dict) else None
        if name and name not in existing_tags:
            existing_tags[name] = t
    if existing_tags:
        combined["tags"] = list(existing_tags.values())

    return JsonResponse(combined)
