from types import SimpleNamespace

from django.test import TestCase

from apps.main.pos_views import _should_use_main_stock_in_agent_sale
from apps.users.models import Roles


class PosOwnerAgentSaleTests(TestCase):
    def test_owner_uses_main_stock_even_when_agent_selected(self):
        owner = SimpleNamespace(role=Roles.OWNER, id="owner-id", company=None)
        selected_agent = SimpleNamespace(id="agent-id")

        self.assertTrue(
            _should_use_main_stock_in_agent_sale(user=owner, acting_agent=selected_agent)
        )
