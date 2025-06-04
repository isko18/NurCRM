def get_filtered_contacts(queryset, params):
    """
    Переиспользуемая функция для фильтрации контактов по параметрам.
    """
    name = params.get('name')
    email = params.get('email')
    company = params.get('company')
    department = params.get('department')

    if name:
        queryset = queryset.filter(name__icontains=name)
    if email:
        queryset = queryset.filter(email__icontains=email)
    if company:
        queryset = queryset.filter(company__icontains=company)
    if department:
        queryset = queryset.filter(department__icontains=department)

    return queryset
