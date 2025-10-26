from ninja.security import HttpBearer
from rest_framework_simplejwt.authentication import JWTAuthentication

class DRFJWTAuth(HttpBearer):
    def authenticate(self, request, token):
        authenticator = JWTAuthentication()
        try:
            validated = authenticator.get_validated_token(token)
            user = authenticator.get_user(validated)
            request.user = user
            return user
        except Exception:
            return None
