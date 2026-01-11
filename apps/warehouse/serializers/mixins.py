from apps.warehouse.utils import _active_branch




class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и проставляет их из контекста на create/update.
    Правило:
      - есть активный филиал → branch = этот филиал
      - нет филиала → branch = NULL (глобально)
    """
    def _user(self):
        req = self.context.get("request")
        return getattr(req, "user", None) if req else None

    def _user_company(self):
        u = self._user()
        return getattr(u, "company", None) or getattr(u, "owned_company", None)

    def _auto_branch(self):
        return _active_branch(self)

    def create(self, validated_data):
        company = self._user_company()
        if company:
            validated_data.setdefault("company", company)
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company:
            validated_data["company"] = company
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)

