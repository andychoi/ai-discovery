CREATE OR ALTER PROCEDURE billing.CloseInvoice
    @invoiceId INT
AS
    UPDATE billing.Invoices SET Status = 'CLOSED', ClosedAt = SYSUTCDATETIME() WHERE Id = @invoiceId;
    DELETE FROM billing.PendingCharges WHERE InvoiceId = @invoiceId;
GO
