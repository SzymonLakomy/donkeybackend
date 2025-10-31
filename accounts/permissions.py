from rest_framework import permissions

class IsManager(permissions.BasePermission):
    """
    Pozwala na dostęp tylko menedżerom i właścicielom.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['manager', 'owner']

class IsManagerForOwnCompany(permissions.BasePermission):
    """
    Pozwala menedżerowi na operacje na użytkownikach tylko z jego firmy.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['manager', 'owner']
    
    def has_object_permission(self, request, view, obj):
        # Sprawdza, czy użytkownik jest z tej samej firmy
        return obj.company == request.user.company
        
class CannotPromoteToOwner(permissions.BasePermission):
    """
    Blokuje możliwość nadania roli 'owner'.
    """
    def has_permission(self, request, view):
        if request.method in ['PUT', 'PATCH'] and 'role' in request.data:
            return request.data['role'] != 'owner'
        return True
