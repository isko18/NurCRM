from rest_framework import permissions

class IsCompanyOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, 'owned_company')
