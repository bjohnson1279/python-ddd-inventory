import pytest
from src.infrastructure.services.report_generator_service import ReportGeneratorService
from src.application.retail_reports import export_csv

@pytest.mark.asyncio
async def test_generation_requires_persisted_owned_job():
    with pytest.raises(ValueError, match='session and tenant'):
        await ReportGeneratorService().generate_report('definition', 'execution', 'json')

@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['pdf', 'xlsx', '../csv'])
async def test_no_format_fallback_or_path_based_report_storage(format):
    with pytest.raises(ValueError, match='CSV and JSON'):
        await ReportGeneratorService(object(), 'tenant').generate_report('definition', 'execution', format)

def test_csv_neutralizes_formula_text_and_preserves_integer_cents():
    csv = export_csv({'columns': ['sku', 'cents'], 'rows': [{'sku': '=HYPERLINK("bad")', 'cents': -101}]})
    assert "'=HYPERLINK" in csv and '-101' in csv
