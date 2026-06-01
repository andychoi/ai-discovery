const OrderService = require('./services/OrderService');
const PaymentService = require('./services/PaymentService');

class CheckoutHandler {
    constructor() {
        this.orderService = new OrderService();
        this.paymentService = new PaymentService();
    }
    run() {
        this.orderService.process();
    }
}
module.exports = CheckoutHandler;
