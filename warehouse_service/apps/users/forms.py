from django import forms
from .models import Company, Industry, Sector

class CompanyAdminForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'industry' in self.data:
            try:
                industry_id = self.data.get('industry')
                industry = Industry.objects.get(id=industry_id)
                self.fields['sector'].queryset = industry.sectors.all()
            except (Industry.DoesNotExist, ValueError, TypeError):
                self.fields['sector'].queryset = Sector.objects.none()
        elif self.instance.pk and self.instance.industry:
            self.fields['sector'].queryset = self.instance.industry.sectors.all()
        else:
            self.fields['sector'].queryset = Sector.objects.none()
