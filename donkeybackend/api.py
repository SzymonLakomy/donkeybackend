from ninja import NinjaAPI
from schedule.api import api as schedule_api

# Main Ninja API instance for the project
api = NinjaAPI(
    title="Donkey Ninja API",
    version="1.0.0",
    docs_url="/docs",
    csrf=False,
)

# Mount routers from apps
api.add_router("/schedule", schedule_api)
