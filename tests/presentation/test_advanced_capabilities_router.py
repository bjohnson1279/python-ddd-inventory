from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from src.domain.ai_models.copilot import WarehouseDigitalTwin, ConversationalAICopilot
from src.presentation import advanced_capabilities_router as acr


def _fake_request(tenant_id="tenant-adv"):
    return SimpleNamespace(state=SimpleNamespace(tenant_id=tenant_id))


def test_copilot_and_digital_twin_domain_unit():
    twin = WarehouseDigitalTwin()
    sim = twin.simulate_stress_test(1000)
    assert sim["max_throughput"] == 800.0
    assert "Pack Station 3" in sim["bottlenecks_detected"]

    copilot = ConversationalAICopilot()
    assert "50 units" in copilot.process_natural_language_query("How much stock is left?")
    assert "AI assistant" in copilot.process_natural_language_query("Hello there")


def test_advanced_capabilities_http_routes(client):
    # 4.3 Multi-location order routing
    r_route = client.post(
        "/api/routing/allocate",
        json={
            "customerLat": 37.7749,
            "customerLon": -122.4194,
            "locations": [
                {"id": "WH-SF", "lat": 37.78, "lon": -122.41},
                {"id": "WH-NY", "lat": 40.71, "lon": -74.00},
            ],
            "stockLevels": [
                {"locationId": "WH-SF", "sku": "SKU-R1", "availableQty": 4},
                {"locationId": "WH-NY", "sku": "SKU-R1", "availableQty": 10},
            ],
            "orderLines": [{"sku": "SKU-R1", "quantity": 6}],
        },
    )
    assert r_route.status_code == 200, r_route.text
    allocs = r_route.json()["allocations"]
    assert allocs["WH-SF"] == [{"sku": "SKU-R1", "quantity": 4}]
    assert allocs["WH-NY"] == [{"sku": "SKU-R1", "quantity": 2}]

    # Insufficient stock returns 409
    r_route_fail = client.post(
        "/api/routing/allocate",
        json={
            "customerLat": 37.77,
            "customerLon": -122.41,
            "locations": [{"id": "WH-SF", "lat": 37.78, "lon": -122.41}],
            "stockLevels": [{"locationId": "WH-SF", "sku": "SKU-R1", "availableQty": 1}],
            "orderLines": [{"sku": "SKU-R1", "quantity": 5}],
        },
    )
    assert r_route_fail.status_code == 409

    # 11.3 Thermal printing & AR guidance
    r_zpl = client.post("/api/hardware/print-thermal", json={"sku": "SKU-Z1", "lotNumber": "LOT-99"})
    assert r_zpl.status_code == 200
    assert "^XA" in r_zpl.json()["zpl"] and "LOT-99" in r_zpl.json()["zpl"]

    r_ar = client.get("/api/hardware/ar-guidance/BIN-A1")
    assert r_ar.status_code == 200
    assert r_ar.json()["overlay"]["highlight"] == "green"

    # 11.4 Digital twin & Copilot
    r_twin = client.post("/api/digital-twin/simulate", json={"throughput": 500})
    assert r_twin.status_code == 200
    assert r_twin.json()["max_throughput"] == 400.0

    r_cop = client.post("/api/copilot/query", json={"query": "Check stock"})
    assert r_cop.status_code == 200
    assert "50 units" in r_cop.json()["response"]

    # 11.5 ESG Carbon Footprint
    r_esg = client.post(
        "/api/sustainability/emissions-report",
        json={"distanceKm": 1000.0, "weightKg": 500.0, "mode": "AIR"},
    )
    assert r_esg.status_code == 200
    assert r_esg.json()["scope3KgCo2"] == pytest.approx(545.0)

    # 16.1 Intercompany pricing & journals
    r_icp = client.post(
        "/api/intercompany/transfer-price",
        json={
            "sourceEntityId": "ENT-US",
            "destinationEntityId": "ENT-EU",
            "baseUnitCostCents": 1000,
            "ruleType": "COST_PLUS",
            "markupPercentage": 15.0,
        },
    )
    assert r_icp.status_code == 200
    assert r_icp.json()["transferPriceCents"] == 1150

    r_icp_bad = client.post(
        "/api/intercompany/transfer-price",
        json={
            "sourceEntityId": "ENT-US",
            "destinationEntityId": "ENT-EU",
            "baseUnitCostCents": 1000,
            "ruleType": "INVALID_RULE",
        },
    )
    assert r_icp_bad.status_code == 422

    r_icj = client.post(
        "/api/intercompany/journal-entries",
        json={
            "sourceEntityId": "ENT-US",
            "destinationEntityId": "ENT-EU",
            "sku": "SKU-IC1",
            "quantity": 10,
            "unitCostCents": 1000,
            "transferPriceCents": 1150,
        },
    )
    assert r_icj.status_code == 200
    assert len(r_icj.json()["shipmentEntries"]) == 3
    assert len(r_icj.json()["receiptEntries"]) == 2

    # 16.2 Billing overages
    r_bill = client.post(
        "/api/billing/evaluate-overages",
        json={
            "tierName": "PRO",
            "maxRequestsPerMinute": 60,
            "includedApiRequestsPerMonth": 1000,
            "maxActiveSkus": 100,
            "apiRequestsCount": 1250,
            "activeSkusCount": 120,
        },
    )
    assert r_bill.status_code == 200
    assert len(r_bill.json()["events"]) == 2

    r_bill_bad = client.post(
        "/api/billing/evaluate-overages",
        json={
            "tierName": "INVALID",
            "maxRequestsPerMinute": 60,
            "includedApiRequestsPerMonth": 1000,
            "maxActiveSkus": 100,
            "apiRequestsCount": 10,
            "activeSkusCount": 10,
        },
    )
    assert r_bill_bad.status_code == 422

    # 17.1 Computer Vision QA Gateway
    r_cv_pass = client.post(
        "/api/cv/analyze",
        json={"dockStationId": "DOCK-1", "imageUrl": "https://cdn.local/box-ok.jpg"},
    )
    assert r_cv_pass.status_code == 200
    assert r_cv_pass.json()["status"] == "PASSED"

    r_cv_flag = client.post(
        "/api/cv/analyze",
        json={"dockStationId": "DOCK-1", "imageUrl": "https://cdn.local/box-damaged.jpg"},
    )
    assert r_cv_flag.status_code == 200
    assert r_cv_flag.json()["status"] == "FLAGGED"

    # 18.1 Dynamic Pricing / Yield
    r_yield = client.post(
        "/api/yield/recommend-markdown",
        json={
            "sku": "SKU-Y1",
            "basePriceCents": 5000,
            "holdingCostPerDayCents": 50,
            "minFloorPriceCents": 2000,
            "daysInInventory": 20,
            "daysUntilExpiration": 5,
            "historicalDailyDemand": 2.0,
            "currentStockQuantity": 50,
        },
    )
    assert r_yield.status_code == 200
    assert r_yield.json()["recommendation"]["recommendedPriceCents"] == 2000

    r_yield_none = client.post(
        "/api/yield/recommend-markdown",
        json={
            "sku": "SKU-Y2",
            "basePriceCents": 5000,
            "holdingCostPerDayCents": 0,
            "minFloorPriceCents": 2000,
            "daysInInventory": 0,
            "historicalDailyDemand": 5.0,
            "currentStockQuantity": 10,
        },
    )
    assert r_yield_none.status_code == 200
    assert r_yield_none.json()["recommendation"] is None


@pytest.mark.asyncio
async def test_advanced_capabilities_handlers_direct_async():
    req = _fake_request("tenant-direct")

    res = await acr.allocate_order_routing(
        acr.OrderRoutingRequestDTO(
            customerLat=0.0,
            customerLon=0.0,
            locations=[acr.RoutingLocationDTO(id="L1", lat=0.1, lon=0.1)],
            stockLevels=[acr.RoutingStockDTO(locationId="L1", sku="SKU-1", availableQty=5)],
            orderLines=[acr.RoutingOrderLineDTO(sku="SKU-1", quantity=3)],
        ),
        req,
    )
    assert res["allocations"]["L1"][0]["quantity"] == 3

    with pytest.raises(HTTPException) as exc:
        await acr.allocate_order_routing(
            acr.OrderRoutingRequestDTO(
                customerLat=0.0,
                customerLon=0.0,
                locations=[acr.RoutingLocationDTO(id="L1", lat=0.1, lon=0.1)],
                stockLevels=[acr.RoutingStockDTO(locationId="L1", sku="SKU-1", availableQty=1)],
                orderLines=[acr.RoutingOrderLineDTO(sku="SKU-1", quantity=5)],
            ),
            req,
        )
    assert exc.value.status_code == 409

    assert "^XA" in (await acr.print_thermal_label(acr.ThermalPrintRequestDTO(sku="S1", lotNumber="L1"), req))["zpl"]
    assert (await acr.get_ar_guidance("BIN-1", req))["overlay"]["x"] == 100
    assert (await acr.simulate_digital_twin(acr.DigitalTwinSimulateDTO(throughput=100), req))["max_throughput"] == 80.0
    assert "AI assistant" in (await acr.query_copilot(acr.CopilotQueryDTO(query="hi"), req))["response"]
    assert (await acr.calculate_esg_emissions(acr.EsgEmissionsRequestDTO(distanceKm=100, weightKg=1000, mode="ROAD"), req))["scope3KgCo2"] == 10.0

    assert (
        await acr.calculate_intercompany_price(
            acr.IntercompanyPriceDTO(sourceEntityId="A", destinationEntityId="B", baseUnitCostCents=100, ruleType="COST_PLUS", markupPercentage=10),
            req,
        )
    )["transferPriceCents"] == 110
    with pytest.raises(HTTPException) as bad_rule:
        await acr.calculate_intercompany_price(
            acr.IntercompanyPriceDTO(sourceEntityId="A", destinationEntityId="B", baseUnitCostCents=100, ruleType="NOPE"),
            req,
        )
    assert bad_rule.value.status_code == 422

    journals = await acr.generate_intercompany_journals(
        acr.IntercompanyJournalDTO(sourceEntityId="A", destinationEntityId="B", sku="S1", quantity=2, unitCostCents=100, transferPriceCents=120),
        req,
    )
    assert len(journals["shipmentEntries"]) == 3

    billing = await acr.evaluate_billing_overages(
        acr.BillingOveragesDTO(
            maxRequestsPerMinute=60,
            includedApiRequestsPerMonth=10,
            maxActiveSkus=10,
            apiRequestsCount=20,
            activeSkusCount=5,
        ),
        req,
    )
    assert len(billing["events"]) == 1

    cv = await acr.analyze_vision_inspection(acr.VisionAnalyzeDTO(dockStationId="D1", imageUrl="http://img/ok.png"), req)
    assert cv["status"] == "PASSED"

    y_rec = await acr.recommend_yield_markdown(
        acr.YieldMarkdownRequestDTO(
            sku="S1",
            basePriceCents=1000,
            holdingCostPerDayCents=10,
            minFloorPriceCents=500,
            daysInInventory=10,
            historicalDailyDemand=1.0,
            currentStockQuantity=5,
        ),
        req,
    )
    assert y_rec["recommendation"]["recommendedPriceCents"] == 900

    y_none = await acr.recommend_yield_markdown(
        acr.YieldMarkdownRequestDTO(
            sku="S1",
            basePriceCents=1000,
            holdingCostPerDayCents=0,
            minFloorPriceCents=500,
            daysInInventory=0,
            historicalDailyDemand=1.0,
            currentStockQuantity=5,
        ),
        req,
    )
    assert y_none["recommendation"] is None


def test_forecasting_and_approval_workflows_http_routes(client):
    # 5.3 / 6.3 / 9.1 Forecasting, slotting & anomalies
    r_rop = client.post(
        "/api/forecasting/rop",
        json={"sku": "SKU-F1", "historicalSales": [10, 20, 30], "leadTimeDays": 5},
    )
    assert r_rop.status_code == 200
    assert r_rop.json()["reorderPoint"] == 150.0

    r_slot = client.post(
        "/api/forecasting/slotting",
        json={"warehouseMap": {"zones": ["A", "B"]}, "skuVelocity": {"SKU-FAST": 150.0, "SKU-SLOW": 10.0}},
    )
    assert r_slot.status_code == 200
    assert len(r_slot.json()["recommendations"]) == 1
    assert r_slot.json()["recommendations"][0]["sku"] == "SKU-FAST"

    r_anom = client.post(
        "/api/forecasting/anomalies",
        json={
            "recentTransactions": [
                {"sku": "SKU-A1", "quantityAdjustment": -80, "timestamp": "2026-10-12T03:00:00Z"},
                {"sku": "SKU-A2", "quantityAdjustment": -5, "timestamp": "2026-10-12T12:00:00Z"},
            ]
        },
    )
    assert r_anom.status_code == 200
    assert len(r_anom.json()["anomalies"]) == 1
    assert r_anom.json()["anomalies"][0]["sku"] == "SKU-A1"

    r_anom_bad = client.post(
        "/api/forecasting/anomalies",
        json={"recentTransactions": [{"sku": "SKU-A1", "quantityAdjustment": -80, "timestamp": "bad-ts"}]},
    )
    assert r_anom_bad.status_code == 422

    # 12.2 Approval Workflows
    r_tpl = client.post(
        "/api/workflows/templates",
        json={"eventName": "HIGH_VALUE_ADJUSTMENT", "requiredRoles": ["MANAGER", "FINANCE"]},
    )
    assert r_tpl.status_code == 201

    r_start_missing = client.post("/api/workflows/start", json={"eventName": "UNKNOWN_EVENT", "payload": {}})
    assert r_start_missing.status_code == 404

    r_start = client.post(
        "/api/workflows/start",
        json={"eventName": "HIGH_VALUE_ADJUSTMENT", "payload": {"sku": "SKU-1", "delta": -100}},
    )
    assert r_start.status_code == 201
    exec_id = r_start.json()["executionId"]
    assert r_start.json()["status"] == "IN_PROGRESS"

    r_get = client.get(f"/api/workflows/{exec_id}")
    assert r_get.status_code == 200
    assert r_get.json()["executionId"] == exec_id

    r_app1 = client.post(
        f"/api/workflows/{exec_id}/approve",
        json={"role": "MANAGER", "userId": "u-mgr", "comments": "Verified"},
    )
    assert r_app1.status_code == 200
    assert r_app1.json()["status"] == "IN_PROGRESS"

    r_app2 = client.post(
        f"/api/workflows/{exec_id}/approve",
        json={"role": "FINANCE", "userId": "u-fin", "comments": "Approved"},
    )
    assert r_app2.status_code == 200
    assert r_app2.json()["status"] == "APPROVED"

    # Rejecting an already approved execution returns 409
    r_rej_conflict = client.post(
        f"/api/workflows/{exec_id}/reject",
        json={"role": "MANAGER", "userId": "u-mgr", "comments": "Too late"},
    )
    assert r_rej_conflict.status_code == 409

    # Start a second workflow and reject it
    r_start2 = client.post("/api/workflows/start", json={"eventName": "HIGH_VALUE_ADJUSTMENT", "payload": {}})
    exec_id2 = r_start2.json()["executionId"]
    r_rej = client.post(
        f"/api/workflows/{exec_id2}/reject",
        json={"role": "MANAGER", "userId": "u-mgr", "comments": "Insufficient proof"},
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_forecasting_and_workflow_handlers_direct_async():
    req_a = _fake_request("tenant-wf-a")
    req_b = _fake_request("tenant-wf-b")

    rop = await acr.calculate_forecasting_rop(
        acr.ForecastingRopDTO(sku="S1", historicalSales=[5, 10], leadTimeDays=2),
        req_a,
    )
    assert rop["reorderPoint"] == 20.0

    slot = await acr.optimize_forecasting_slotting(
        acr.ForecastingSlottingDTO(warehouseMap={}, skuVelocity={"S1": 200.0}),
        req_a,
    )
    assert len(slot["recommendations"]) == 1

    anom = await acr.detect_forecasting_anomalies(
        acr.ForecastingAnomaliesDTO(
            recentTransactions=[acr.ForecastingTransactionDTO(sku="S1", quantityAdjustment=-99, timestamp="2026-10-01T02:00:00Z")]
        ),
        req_a,
    )
    assert len(anom["anomalies"]) == 1

    with pytest.raises(HTTPException) as bad_ts:
        await acr.detect_forecasting_anomalies(
            acr.ForecastingAnomaliesDTO(
                recentTransactions=[acr.ForecastingTransactionDTO(sku="S1", quantityAdjustment=-99, timestamp="invalid")]
            ),
            req_a,
        )
    assert bad_ts.value.status_code == 422

    await acr.register_workflow_template(
        acr.WorkflowTemplateDTO(eventName="PO_APPROVAL", requiredRoles=["MANAGER"]),
        req_a,
    )
    started = await acr.start_approval_workflow(acr.WorkflowStartDTO(eventName="PO_APPROVAL", payload={"po": "1"}), req_a)
    eid = started["executionId"]

    # Cross-tenant isolation: tenant-wf-b cannot read/approve/reject tenant-wf-a's execution
    with pytest.raises(HTTPException) as not_found_get:
        await acr.get_approval_workflow(eid, req_b)
    assert not_found_get.value.status_code == 404

    with pytest.raises(HTTPException) as not_found_app:
        await acr.approve_workflow_step(eid, acr.WorkflowDecisionDTO(role="MANAGER", userId="u1"), req_b)
    assert not_found_app.value.status_code == 404

    with pytest.raises(HTTPException) as not_found_rej:
        await acr.reject_workflow_step(eid, acr.WorkflowDecisionDTO(role="MANAGER", userId="u1"), req_b)
    assert not_found_rej.value.status_code == 404

    with pytest.raises(HTTPException) as missing_tpl:
        await acr.start_approval_workflow(acr.WorkflowStartDTO(eventName="MISSING", payload={}), req_a)
    assert missing_tpl.value.status_code == 404

    fetched = await acr.get_approval_workflow(eid, req_a)
    assert fetched["status"] == "IN_PROGRESS"

    approved = await acr.approve_workflow_step(eid, acr.WorkflowDecisionDTO(role="MANAGER", userId="u1"), req_a)
    assert approved["status"] == "APPROVED"

    with pytest.raises(HTTPException) as conflict_app:
        await acr.approve_workflow_step(eid, acr.WorkflowDecisionDTO(role="MANAGER", userId="u1"), req_a)
    assert conflict_app.value.status_code == 409

    started2 = await acr.start_approval_workflow(acr.WorkflowStartDTO(eventName="PO_APPROVAL", payload={}), req_a)
    rejected = await acr.reject_workflow_step(started2["executionId"], acr.WorkflowDecisionDTO(role="MANAGER", userId="u2"), req_a)
    assert rejected["status"] == "REJECTED"

    with pytest.raises(HTTPException) as conflict_rej:
        await acr.reject_workflow_step(started2["executionId"], acr.WorkflowDecisionDTO(role="MANAGER", userId="u2"), req_a)
    assert conflict_rej.value.status_code == 409
