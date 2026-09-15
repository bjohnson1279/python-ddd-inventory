class DomainException(Exception):
    pass

class InvalidSKUError(DomainException):
    pass

class InvalidPriceError(DomainException):
    pass

class ProductNotFoundError(DomainException):
    pass

