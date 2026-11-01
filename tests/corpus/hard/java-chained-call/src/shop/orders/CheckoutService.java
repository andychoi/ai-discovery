package shop.orders;

public class CheckoutService {
    private final OrderRepository orders = new OrderRepository();

    public long amountDue(long id) {
        return orders.find(id).total();
    }
}
