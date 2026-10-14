from pydantic import BaseModel, model_validator


class BaseUnitDTO(BaseModel):
    """Fail explicitly when a base-unit-only workflow receives a named unit."""
    @model_validator(mode='before')
    @classmethod
    def require_base_units(cls, value):
        if isinstance(value, dict) and any(key in value for key in ('unit', 'uomVersion', 'uom_version')):
            raise ValueError('This workflow accepts integral base units; convert named units first')
        return value
