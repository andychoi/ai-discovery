using App.Services;
namespace App.Handlers {
    public class CheckoutHandler {
        private readonly OrderService _orderService;
        public CheckoutHandler(OrderService orderService) { _orderService = orderService; }
        public void Run() { _orderService.Process(); }
    }
}
