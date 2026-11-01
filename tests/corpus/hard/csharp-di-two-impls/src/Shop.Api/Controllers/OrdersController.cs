using Shop.Api.Services;

namespace Shop.Api.Controllers;

public class OrdersController
{
    private readonly IOrderService _orders;

    public OrdersController(IOrderService orders)
    {
        _orders = orders;
    }

    public int Create(int productId)
    {
        return _orders.Place(productId);
    }
}
