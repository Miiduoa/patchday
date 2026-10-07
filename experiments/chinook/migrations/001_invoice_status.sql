ALTER TABLE Invoice ADD COLUMN PaymentStatus TEXT NOT NULL DEFAULT 'pending'
    CHECK (PaymentStatus IN ('pending', 'paid'));
CREATE INDEX invoice_status_date ON Invoice(PaymentStatus, InvoiceDate);
