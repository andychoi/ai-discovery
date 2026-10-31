using Microsoft.Extensions.Logging;

namespace Shop;

public class OrderService
{
    private readonly ILogger<OrderService> _logger;
    private readonly AuditLog _audit = new AuditLog();

    public OrderService(ILogger<OrderService> logger) { _logger = logger; }

    public void Place()
    {
        _logger.LogInformation("order placed");
    }

    public void Audit()
    {
        _audit.LogInformation("audited");
    }
}
