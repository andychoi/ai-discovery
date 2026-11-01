namespace Shop.Api.Services;

// Kept for a future caching experiment; not registered in the container.
public class CachedOrderService : IOrderService
{
    public int Place(int productId)
    {
        return -productId;
    }
}
