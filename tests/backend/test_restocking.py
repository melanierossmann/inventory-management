"""
Tests for restocking order API endpoints.
"""
import pytest


def _inventory_by_sku(client):
    return {item["sku"]: item for item in client.get("/api/inventory").json()}


class TestRestockingData:
    """Restocking relies on every forecast SKU having an inventory record."""

    def test_all_forecast_skus_exist_in_inventory(self, client):
        """Test that each demand forecast SKU has inventory data (cost and stock)."""
        inventory = _inventory_by_sku(client)
        forecasts = client.get("/api/demand").json()
        assert len(forecasts) > 0

        for forecast in forecasts:
            assert forecast["item_sku"] in inventory, f"{forecast['item_sku']} missing from inventory"
            assert inventory[forecast["item_sku"]]["unit_cost"] > 0


class TestRestockingOrders:
    """Test suite for restocking order endpoints."""

    def test_get_restock_orders_initially_empty(self, client):
        """Test that no restocking orders exist before any are submitted."""
        response = client.get("/api/restocking-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order(self, client):
        """Test submitting a valid restocking order."""
        inventory = _inventory_by_sku(client)
        payload = {
            "budget": 5000,
            "lines": [
                {"sku": "WDG-001", "quantity": 10},
                {"sku": "SNR-420", "quantity": 5},
            ],
        }
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["status"] == "Submitted"
        assert order["budget"] == 5000
        assert len(order["lines"]) == 2

        expected_total = 10 * inventory["WDG-001"]["unit_cost"] + 5 * inventory["SNR-420"]["unit_cost"]
        assert abs(order["total_cost"] - expected_total) < 0.01
        assert "T" in order["expected_delivery"]

    def test_order_lead_time_is_slowest_line(self, client):
        """Test that the order lead time equals the longest line lead time."""
        payload = {
            "budget": 5000,
            "lines": [
                {"sku": "WDG-001", "quantity": 1},
                {"sku": "SNR-420", "quantity": 1},
            ],
        }
        order = client.post("/api/restocking-orders", json=payload).json()

        line_times = [line["lead_time_days"] for line in order["lines"]]
        assert len(set(line_times)) > 1, "test needs lines with different lead times"
        assert order["lead_time_days"] == max(line_times)

    def test_server_uses_inventory_cost_not_client_value(self, client):
        """Test that a client-supplied unit_cost cannot change the price."""
        inventory = _inventory_by_sku(client)
        payload = {
            "budget": 5000,
            "lines": [{"sku": "WDG-001", "quantity": 2, "unit_cost": 0.01}],
        }
        order = client.post("/api/restocking-orders", json=payload).json()

        assert order["lines"][0]["unit_cost"] == inventory["WDG-001"]["unit_cost"]

    def test_total_equal_to_budget_is_accepted(self, client):
        """Test that spending exactly the budget is allowed."""
        inventory = _inventory_by_sku(client)
        budget = round(4 * inventory["WDG-001"]["unit_cost"], 2)
        response = client.post(
            "/api/restocking-orders",
            json={"budget": budget, "lines": [{"sku": "WDG-001", "quantity": 4}]},
        )
        assert response.status_code == 201

    def test_over_budget_rejected(self, client):
        """Test that an order above the budget returns 400 and is not stored."""
        response = client.post(
            "/api/restocking-orders",
            json={"budget": 10, "lines": [{"sku": "MTR-304", "quantity": 5}]},
        )
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restocking-orders").json() == []

    def test_empty_lines_rejected(self, client):
        """Test that an order with no lines returns 400."""
        response = client.post("/api/restocking-orders", json={"budget": 1000, "lines": []})
        assert response.status_code == 400

    def test_unknown_sku_rejected(self, client):
        """Test that an unknown SKU returns 400."""
        response = client.post(
            "/api/restocking-orders",
            json={"budget": 1000, "lines": [{"sku": "NOPE-999", "quantity": 1}]},
        )
        assert response.status_code == 400
        assert "unknown sku" in response.json()["detail"].lower()

    @pytest.mark.parametrize("quantity", [0, -3])
    def test_non_positive_quantity_rejected(self, client, quantity):
        """Test that zero or negative quantities return 400."""
        response = client.post(
            "/api/restocking-orders",
            json={"budget": 1000, "lines": [{"sku": "WDG-001", "quantity": quantity}]},
        )
        assert response.status_code == 400

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget returns 400."""
        response = client.post(
            "/api/restocking-orders",
            json={"budget": -1, "lines": [{"sku": "WDG-001", "quantity": 1}]},
        )
        assert response.status_code == 400

    def test_missing_fields_return_422(self, client):
        """Test that a malformed body fails Pydantic validation."""
        response = client.post("/api/restocking-orders", json={"budget": 100})
        assert response.status_code == 422

    def test_get_restock_orders_newest_first(self, client):
        """Test that submitted orders are listed newest first."""
        first = client.post(
            "/api/restocking-orders",
            json={"budget": 1000, "lines": [{"sku": "WDG-001", "quantity": 1}]},
        ).json()
        second = client.post(
            "/api/restocking-orders",
            json={"budget": 1000, "lines": [{"sku": "SNR-420", "quantity": 1}]},
        ).json()

        data = client.get("/api/restocking-orders").json()
        assert [o["order_number"] for o in data] == [second["order_number"], first["order_number"]]

    def test_restock_orders_do_not_affect_sales_orders(self, client):
        """Test that restocking orders stay out of the sales orders and dashboard."""
        before_orders = len(client.get("/api/orders").json())
        before_summary = client.get("/api/dashboard/summary").json()

        client.post(
            "/api/restocking-orders",
            json={"budget": 1000, "lines": [{"sku": "WDG-001", "quantity": 1}]},
        )

        assert len(client.get("/api/orders").json()) == before_orders
        assert client.get("/api/dashboard/summary").json() == before_summary
