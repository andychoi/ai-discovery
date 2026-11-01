using System.Data;
using Dapper;

namespace Billing
{
    public class InvoiceStore
    {
        private readonly IDbConnection _db;

        public InvoiceStore(IDbConnection db)
        {
            _db = db;
        }

        public void Close(int invoiceId)
        {
            _db.Execute("billing.CloseInvoice", new { invoiceId }, commandType: CommandType.StoredProcedure);
        }
    }
}
