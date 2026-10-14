from src.domain.product.layer_costing import MAX_SAFE_CENTS

ACCOUNTS = {
    '1000': ('Cash', 'asset'), '1100': ('Accounts receivable', 'asset'),
    '1200': ('Inventory', 'asset'), '1210': ('Inventory in transit', 'asset'),
    '1220': ('Kit work in progress', 'asset'), '1300': ('Supplier credits receivable', 'asset'),
    '2000': ('Accounts payable', 'liability'), '2100': ('Goods received not invoiced', 'liability'),
    '3000': ('Opening equity', 'equity'), '4000': ('Sales revenue', 'revenue'),
    '4100': ('Sales returns', 'revenue'), '5000': ('Cost of goods sold', 'expense'),
    '5100': ('Inventory loss', 'expense'), '5200': ('Inventory variance', 'expense'),
    '1500': ('Equipment', 'asset'), '2300': ('Loans payable', 'liability'),
    '3100': ('Owner capital', 'equity'), '3200': ('Retained earnings', 'equity'),
    '6000': ('Operating expenses', 'expense'), '6100': ('Payroll expense', 'expense'),
    '6200': ('Rent expense', 'expense'), '6300': ('Bank and merchant fees', 'expense'),
}


def checked_cents(value, *, signed=False):
    if type(value) is not int or not (-MAX_SAFE_CENTS if signed else 0) <= value <= MAX_SAFE_CENTS:
        raise ValueError('Amount must be exact integer cents within the supported limit')
    return value


def validate_lines(lines):
    if not 2 <= len(lines) <= 100:
        raise ValueError('A journal requires 2–100 lines')
    totals = {'debit': 0, 'credit': 0}
    for line in lines:
        if line['accountCode'] not in ACCOUNTS or line['type'] not in totals:
            raise ValueError('Unknown account code or journal side')
        value = checked_cents(line['amountCents'])
        if value == 0:
            raise ValueError('Journal lines must have positive amounts')
        totals[line['type']] += value
    if totals['debit'] != totals['credit']:
        raise ValueError('Journal debits must equal credits')
    checked_cents(totals['debit'])
    return totals['debit']


def pair(debit, credit, amount):
    return [(debit, 'debit', amount), (credit, 'credit', amount)]


def movement_facts(operation, amount, result, receipt_value=None):
    """Convert persisted physical facts, never catalog prices, into postings."""
    kind, postings = 'INTERNAL', []
    if operation == 'receive' or operation == 'serial-receive' and result.get('fromStatus') == 'pending':
        kind, postings = 'RECEIPT', pair('1200', '2100', receipt_value)
    elif operation in ('dispatch', 'fulfill-allocation', 'serial-sell', 'kit-sale-component'):
        kind, postings = 'SALE', pair('5000', '1200', result.get('costOfGoodsSoldCents'))
    elif operation in ('customer-return', 'serial-return'):
        kind = 'CUSTOMER_RETURN'
        value = result.get('cogsReversedCents')
        loss = result.get('writeOffCents', 0)
        postings = [('1200', 'debit', value - loss if value is not None and loss is not None else None),
                    ('5100', 'debit', loss), ('5000', 'credit', value)]
    elif operation == 'supplier-return':
        kind = 'SUPPLIER_RETURN'
        credit = result.get('supplierCreditExpectedCents')
        change = result.get('inventoryValueChangeCents')
        carried = -change if change is not None else None
        postings = [('1300', 'debit', credit), ('1200', 'credit', carried)]
        if credit is not None and carried is not None:
            difference = carried - credit
            postings.append(('5200', 'debit' if difference >= 0 else 'credit', abs(difference)))
    elif operation in ('write-off', 'quarantine-scrap', 'serial-write-off'):
        kind = 'LOSS'
        change = result.get('inventoryValueChangeCents')
        postings = pair('5100', '1200', -change if change is not None else (0 if amount == 0 else None))
    elif operation in ('adjust', 'price-cost-layer', 'serial-price'):
        kind = 'ADJUSTMENT'
        value = result.get('inventoryValueChangeCents')
        if operation == 'price-cost-layer':
            value = result['unitCostCents'] * result['remainingQuantity']
        expense = '5100' if result.get('reasonCode') in ('DAMAGE', 'THEFT', 'EXPIRED') else '5200'
        postings = pair('1200', expense, value) if value is None or value >= 0 else pair(expense, '1200', -value)
    elif operation in ('transfer-dispatch', 'serial-transfer'):
        kind = 'TRANSIT'
        value = result.get('transferredValueCents')
        postings = pair('1210', '1200', value)
    elif operation in ('transfer-receive', 'transfer-cancel') or operation == 'serial-receive' and result.get('fromStatus') == 'transferred':
        kind = 'TRANSIT'
        postings = pair('1200', '1210', result.get('inventoryValueChangeCents', receipt_value))
    elif operation == 'kit-assemble-component':
        used = result.get('consumedLayers', [])
        value = sum(l['costCents'] for l in used) if used and all(l.get('costCents') is not None for l in used) else None
        postings = pair('1220', '1200', value)
    elif operation in ('kit-assemble', 'kit-disassemble-component'):
        postings = pair('1200', '1220', result.get('carryingValueCents'))
    elif operation == 'kit-disassemble':
        postings = pair('1220', '1200', result.get('carryingValueCents'))
    elif operation not in ('allocate', 'release-allocation', 'serial-reserve', 'serial-release',
                           'serial-quarantine', 'serial-damage', 'serial-restock', 'quarantine-release', 'quarantine-restock'):
        return {'kind': 'REVIEW', 'postings': [], 'unknown': True}
    unknown = any(value is None for _, _, value in postings)
    for _, _, value in postings:
        if value is not None:
            checked_cents(value)
    return {'kind': kind, 'postings': postings, 'unknown': unknown}
