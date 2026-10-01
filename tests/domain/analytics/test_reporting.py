import pytest
import json
import csv
import io

from src.domain.analytics.reporting import SavedView, ReportingEngine

def test_saved_view_initialization():
    view_id = "v1"
    name = "Test View"
    filters = {"status": "active", "type": "report"}

    view = SavedView(view_id=view_id, name=name, filters=filters)

    assert view.view_id == view_id
    assert view.name == name
    assert view.filters == filters

def test_reporting_engine_save_view():
    engine = ReportingEngine()

    view_id = "v1"
    name = "Test View"
    filters = {"status": "active"}

    engine.save_view(view_id=view_id, name=name, filters=filters)

    assert view_id in engine.saved_views
    saved_view = engine.saved_views[view_id]

    assert isinstance(saved_view, SavedView)
    assert saved_view.view_id == view_id
    assert saved_view.name == name
    assert saved_view.filters == filters

def test_reporting_engine_export_csv():
    engine = ReportingEngine()

    headers = ["id", "name", "value"]
    data = [
        {"id": "1", "name": "Item 1", "value": "100"},
        {"id": "2", "name": "Item 2", "value": "200"}
    ]

    csv_str = engine.export_csv(headers=headers, data=data)

    # Verify parsing back the CSV gives expected results
    reader = csv.DictReader(io.StringIO(csv_str))

    assert reader.fieldnames == headers

    parsed_data = list(reader)
    assert len(parsed_data) == 2
    assert parsed_data[0] == data[0]
    assert parsed_data[1] == data[1]

def test_reporting_engine_export_json():
    engine = ReportingEngine()

    data = [
        {"id": "1", "name": "Item 1", "value": 100},
        {"id": "2", "name": "Item 2", "value": 200}
    ]

    json_str = engine.export_json(data=data)

    # Verify parsing back the JSON gives expected results
    parsed_data = json.loads(json_str)

    assert parsed_data == data
    assert len(parsed_data) == 2
