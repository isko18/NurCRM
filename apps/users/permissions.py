from rest_framework import permissions

class IsCompanyOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, 'owned_company')


class IsCompanyOwnerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['owner', 'admin']

    def has_object_permission(self, request, view, obj):
        return (
            request.user.company == obj.company
            and request.user.role in ['owner', 'admin']
            and request.user != obj  # запретить редактировать себя
        )