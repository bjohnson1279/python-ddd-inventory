import uuid
from sqlalchemy import Column, String, Integer, BigInteger, Date, DateTime, Boolean, JSON, ForeignKey, UniqueConstraint, CheckConstraint, Index, event, text
from src.infrastructure.database import Base


class LedgerStateModel(Base):
    __tablename__ = 'python_ledger_state'
    __table_args__ = (CheckConstraint('last_number >= 0 AND last_number <= 9007199254740991 AND version >= 0'),
        CheckConstraint("NOT active OR (method IS NOT NULL AND method IN ('CASH','ACCRUAL') AND activated_at IS NOT NULL)"),)
    tenant_id = Column(String, ForeignKey('python_tenants.id'), primary_key=True)
    active = Column(Boolean, nullable=False, default=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_through = Column(Date, nullable=True)
    method = Column(String, nullable=True)
    last_number = Column(BigInteger, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=0)


class JournalModel(Base):
    __tablename__ = 'python_journals'
    __table_args__ = (UniqueConstraint('tenant_id', 'number'), UniqueConstraint('reversal_of'), UniqueConstraint('source_id'),
        CheckConstraint('total_debits = total_credits AND total_debits > 0 AND total_debits <= 9007199254740991'),
        CheckConstraint('line_count >= 2 AND line_count <= 100'), CheckConstraint('number > 0 AND number <= 9007199254740991'),
        CheckConstraint("method IN ('CASH','ACCRUAL')"),
        CheckConstraint("kind IN ('MANUAL','REVERSAL','INVENTORY','OPENING','SOURCE_RESOLUTION','DOCUMENT','SETTLEMENT','CREDIT','REFUND')"),
        CheckConstraint("kind NOT IN ('INVENTORY','SOURCE_RESOLUTION') OR source_id IS NOT NULL"), Index('ix_python_journal_date', 'tenant_id', 'date', 'number'))
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    number = Column(BigInteger, nullable=False)
    date = Column(Date, nullable=False)
    posted_at = Column(DateTime(timezone=True), nullable=False)
    description = Column(String, nullable=False)
    reference_id = Column(String, nullable=True)
    actor_id = Column(String, nullable=False)
    method = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    source_id = Column(String, nullable=True)
    reversal_of = Column(String, ForeignKey('python_journals.id'), nullable=True)
    lines = Column(JSON, nullable=False)
    line_count = Column(Integer, nullable=False)
    total_debits = Column(BigInteger, nullable=False)
    total_credits = Column(BigInteger, nullable=False)


class JournalLineModel(Base):
    __tablename__ = 'python_journal_lines'
    __table_args__ = (UniqueConstraint('journal_id', 'position'), CheckConstraint('amount_cents > 0 AND amount_cents <= 9007199254740991'),
        CheckConstraint("side IN ('debit','credit')"), Index('ix_python_line_account', 'tenant_id', 'account_code'))
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    journal_id = Column(String, ForeignKey('python_journals.id'), nullable=False)
    position = Column(Integer, nullable=False)
    account_code = Column(String, nullable=False)
    side = Column(String, nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    memo = Column(String, nullable=False)


class FinancialSourceModel(Base):
    __tablename__ = 'python_financial_sources'
    movement_id = Column(String, ForeignKey('python_stock_movements.id'), primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    date = Column(Date, nullable=False)
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False)
    facts = Column(JSON, nullable=False)
    amount_cents = Column(BigInteger, nullable=True)
    journal_id = Column(String, ForeignKey('python_journals.id'), nullable=True)
    version = Column(Integer, nullable=False, default=0)


class FinancialDocumentModel(Base):
    __tablename__ = 'python_financial_documents'
    __table_args__ = (UniqueConstraint('tenant_id', 'kind', 'reference'),
        CheckConstraint('amount_cents > 0 AND settled_cents >= 0 AND settled_cents <= amount_cents AND credited_cents >= 0 AND credited_cents <= amount_cents AND refunded_cents >= 0 AND refunded_cents <= settled_cents'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    reference = Column(String, nullable=False)
    counterparty = Column(String, nullable=False)
    supplier_id = Column(String, ForeignKey('python_suppliers.id'), nullable=True)
    date = Column(Date, nullable=False)
    method = Column(String, nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    settled_cents = Column(BigInteger, nullable=False, default=0)
    credited_cents = Column(BigInteger, nullable=False, default=0)
    refunded_cents = Column(BigInteger, nullable=False, default=0)
    journal_id = Column(String, ForeignKey('python_journals.id'), nullable=True)
    version = Column(Integer, nullable=False, default=0)


class DocumentSourceModel(Base):
    __tablename__ = 'python_document_sources'
    source_id = Column(String, ForeignKey('python_financial_sources.movement_id'), primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    document_id = Column(String, ForeignKey('python_financial_documents.id'), nullable=False)


class SettlementModel(Base):
    __tablename__ = 'python_financial_settlements'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    document_id = Column(String, ForeignKey('python_financial_documents.id'), nullable=False)
    reference = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    journal_id = Column(String, ForeignKey('python_journals.id'), nullable=False)
    direction = Column(String, nullable=False, default='PAYMENT')
    __table_args__ = (UniqueConstraint('tenant_id', 'reference'), CheckConstraint('amount_cents > 0'))


class FinancialCreditModel(Base):
    __tablename__ = 'python_financial_credits'
    __table_args__ = (UniqueConstraint('tenant_id', 'reference'), CheckConstraint('amount_cents > 0 AND amount_cents <= 9007199254740991'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    document_id = Column(String, ForeignKey('python_financial_documents.id'), nullable=False)
    source_id = Column(String, ForeignKey('python_financial_sources.movement_id'), nullable=False)
    reference = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    journal_id = Column(String, ForeignKey('python_journals.id'), nullable=True)


@event.listens_for(JournalModel.__table__, 'after_create')
def install_journal_guards(table, connection, **kwargs):
    # Posted journals are append-only, including for direct SQL runtime access.
    if connection.dialect.name == 'sqlite':
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(text(f"CREATE TRIGGER python_journal_no_{operation.lower()} BEFORE {operation} ON python_journals BEGIN SELECT RAISE(ABORT, 'Posted journals are immutable'); END"))
        connection.execute(text("""CREATE TRIGGER python_journal_validate BEFORE INSERT ON python_journals BEGIN
            SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM python_ledger_state s WHERE s.tenant_id=NEW.tenant_id AND s.active
              AND s.method=NEW.method AND NEW.date >= date(s.activated_at) AND (s.closed_through IS NULL OR NEW.date > s.closed_through)
              AND NEW.date <= date('now')) THEN RAISE(ABORT,'Journal is outside the active open period') END;
            SELECT CASE WHEN NOT json_valid(NEW.lines) THEN RAISE(ABORT,'Invalid journal JSON') END;
            SELECT CASE WHEN json_type(NEW.lines) <> 'array' OR json_array_length(NEW.lines) <> NEW.line_count
              OR EXISTS (SELECT 1 FROM json_each(NEW.lines) WHERE json_type(value,'$.amountCents') IS NOT 'integer'
                 OR json_type(value,'$.type') IS NOT 'text' OR json_type(value,'$.accountCode') IS NOT 'text'
                 OR json_type(value,'$.id') IS NOT 'text' OR json_type(value,'$.memo') IS NOT 'text'
                 OR json_extract(value,'$.amountCents') <= 0 OR json_extract(value,'$.type') NOT IN ('debit','credit')
                 OR json_extract(value,'$.accountCode') NOT IN ('1000','1100','1200','1210','1220','1300','2000','2100','3000','4000','4100','5000','5100','5200','1500','2300','3100','3200','6000','6100','6200','6300'))
              OR (SELECT coalesce(sum(json_extract(value,'$.amountCents')),0) FROM json_each(NEW.lines) WHERE json_extract(value,'$.type')='debit') <> NEW.total_debits
              OR (SELECT coalesce(sum(json_extract(value,'$.amountCents')),0) FROM json_each(NEW.lines) WHERE json_extract(value,'$.type')='credit') <> NEW.total_credits
              THEN RAISE(ABORT,'Unbalanced or inconsistent journal') END; END"""))
    else:
        connection.execute(text("""CREATE OR REPLACE FUNCTION python_validate_journal() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE debits numeric; credits numeric;
        BEGIN
          IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Posted journals are immutable' USING ERRCODE='55000'; END IF;
          IF NOT EXISTS (SELECT 1 FROM python_ledger_state s WHERE s.tenant_id=NEW.tenant_id AND s.active
            AND s.method=NEW.method AND NEW.date >= (s.activated_at AT TIME ZONE 'UTC')::date
            AND (s.closed_through IS NULL OR NEW.date > s.closed_through) AND NEW.date <= (clock_timestamp() AT TIME ZONE 'UTC')::date) THEN
            RAISE EXCEPTION 'Journal is outside the active open period' USING ERRCODE='23514'; END IF;
          IF json_typeof(NEW.lines) <> 'array' OR json_array_length(NEW.lines) <> NEW.line_count
             OR EXISTS (SELECT 1 FROM json_array_elements(NEW.lines) l WHERE json_typeof(l->'amountCents') IS DISTINCT FROM 'number'
                OR json_typeof(l->'type') IS DISTINCT FROM 'string' OR json_typeof(l->'accountCode') IS DISTINCT FROM 'string'
                OR json_typeof(l->'id') IS DISTINCT FROM 'string' OR json_typeof(l->'memo') IS DISTINCT FROM 'string'
                OR l->>'accountCode' NOT IN ('1000','1100','1200','1210','1220','1300','2000','2100','3000','4000','4100','5000','5100','5200','1500','2300','3100','3200','6000','6100','6200','6300')
                OR (l->>'amountCents') !~ '^[1-9][0-9]*$' OR (l->>'type') NOT IN ('debit','credit')) THEN
            RAISE EXCEPTION 'Invalid journal lines' USING ERRCODE='23514'; END IF;
          SELECT coalesce(sum((l->>'amountCents')::numeric) FILTER (WHERE l->>'type'='debit'),0),
                 coalesce(sum((l->>'amountCents')::numeric) FILTER (WHERE l->>'type'='credit'),0)
            INTO debits,credits FROM json_array_elements(NEW.lines) l;
          IF debits <> NEW.total_debits OR credits <> NEW.total_credits THEN
            RAISE EXCEPTION 'Unbalanced or inconsistent journal' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END $$"""))
        connection.execute(text('CREATE TRIGGER python_journal_guard BEFORE INSERT OR UPDATE OR DELETE ON python_journals FOR EACH ROW EXECUTE FUNCTION python_validate_journal()'))


@event.listens_for(JournalLineModel.__table__, 'after_create')
def install_line_guards(table, connection, **kwargs):
    if connection.dialect.name == 'sqlite':
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(text(f"CREATE TRIGGER python_line_no_{operation.lower()} BEFORE {operation} ON python_journal_lines BEGIN SELECT RAISE(ABORT, 'Posted lines are immutable'); END"))
        connection.execute(text("""CREATE TRIGGER python_line_validate BEFORE INSERT ON python_journal_lines BEGIN
          SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM python_journals j WHERE j.id=NEW.journal_id AND j.tenant_id=NEW.tenant_id
            AND NEW.position >= 0 AND NEW.position < j.line_count
            AND json_extract(j.lines,'$[' || NEW.position || '].id')=NEW.id
            AND json_extract(j.lines,'$[' || NEW.position || '].accountCode')=NEW.account_code
            AND json_extract(j.lines,'$[' || NEW.position || '].amountCents')=NEW.amount_cents
            AND json_extract(j.lines,'$[' || NEW.position || '].type')=NEW.side
            AND json_extract(j.lines,'$[' || NEW.position || '].memo')=NEW.memo)
          THEN RAISE(ABORT,'Journal line does not match its owned journal') END; END"""))
    else:
        connection.execute(text("""CREATE OR REPLACE FUNCTION python_validate_journal_line() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Posted lines are immutable' USING ERRCODE='55000'; END IF;
          IF NOT EXISTS (SELECT 1 FROM python_journals j WHERE j.id=NEW.journal_id AND j.tenant_id=NEW.tenant_id
            AND NEW.position >= 0 AND NEW.position < j.line_count
            AND j.lines->NEW.position->>'id'=NEW.id AND j.lines->NEW.position->>'accountCode'=NEW.account_code
            AND (j.lines->NEW.position->>'amountCents')::numeric=NEW.amount_cents
            AND j.lines->NEW.position->>'type'=NEW.side AND j.lines->NEW.position->>'memo'=NEW.memo) THEN
            RAISE EXCEPTION 'Journal line does not match its owned journal' USING ERRCODE='23514'; END IF;
          RETURN NEW; END $$"""))
        connection.execute(text('CREATE TRIGGER python_line_guard BEFORE INSERT OR UPDATE OR DELETE ON python_journal_lines FOR EACH ROW EXECUTE FUNCTION python_validate_journal_line()'))
