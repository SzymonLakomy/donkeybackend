from ninja.security import HttpBearer
from rest_framework_simplejwt.authentication import JWTAuthentication
import os
from django.contrib.auth import get_user_model


class DRFJWTAuth(HttpBearer):
    def authenticate(self, request, token):
        """
        Authenticate requests via DRF SimpleJWT, with an optional short test token.

        Environment variables (.env supported via load_dotenv in settings):
        - TEST_TOKEN_ENABLED: "true"/"1" to enable the short test token (default: disabled)
        - TEST_TOKEN_VALUE: the bearer token string to accept (default: "test")
        - TEST_TOKEN_USER_ID: optional user id to impersonate when using the test token
        - TEST_TOKEN_USER_EMAIL: optional user email to impersonate when using the test token
          If neither ID nor EMAIL provided, it falls back to first superuser, else first staff,
          else the first user in the database.
        """
        # Fast-path: accept a short test token when enabled
        if os.getenv("TEST_TOKEN_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"): 
            test_token = os.getenv("TEST_TOKEN_VALUE", "test")
            if token == test_token:
                User = get_user_model()
                user = None
                user_id = os.getenv("TEST_TOKEN_USER_ID", 1)
                user_email = os.getenv("TEST_TOKEN_USER_EMAIL", "test@wp.pl")
                try:
                    if user_id:
                        try:
                            user = User.objects.filter(id=int(user_id)).first()
                        except ValueError:
                            user = None
                    if user is None and user_email:
                        user = User.objects.filter(email=user_email).first()
                    if user is None:
                        user = (
                            User.objects.filter(is_superuser=True).first()
                            or User.objects.filter(is_staff=True).first()
                            or User.objects.order_by("id").first()
                        )
                except Exception:
                    user = User.objects.order_by("id").first()

                if user is not None:
                    request.user = user
                    return user
                # Enabled but no user to bind to -> reject
                return None

        # Default JWT authentication path
        authenticator = JWTAuthentication()
        try:
            validated = authenticator.get_validated_token(token)
            user = authenticator.get_user(validated)
            request.user = user
            return user
        except Exception:
            return None
