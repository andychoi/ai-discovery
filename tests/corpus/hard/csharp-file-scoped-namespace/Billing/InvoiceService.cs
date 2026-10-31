namespace Shop.Billing;

public class InvoiceService
{
    private readonly Calculator _calc = new Calculator();

    public decimal Total(decimal x)
    {
        return _calc.Compute(x);
    }
}
