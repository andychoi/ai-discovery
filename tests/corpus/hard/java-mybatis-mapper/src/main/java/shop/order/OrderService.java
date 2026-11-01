package shop.order;

import java.util.List;

public class OrderService {
    private final OrderMapper orderMapper;

    public OrderService(OrderMapper orderMapper) {
        this.orderMapper = orderMapper;
    }

    public List<Order> openOrders(long customerId) {
        return orderMapper.findByCustomer(customerId, "OPEN");
    }

    public void place(Order order) {
        orderMapper.insertOrder(order);
    }
}
