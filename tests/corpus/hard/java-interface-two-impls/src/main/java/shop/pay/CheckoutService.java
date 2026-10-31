package shop.pay;

public class CheckoutService {
    private final PaymentGateway gateway;

    public CheckoutService(PaymentGateway gateway) { this.gateway = gateway; }

    public void pay(long cents) {
        gateway.charge(cents);
    }
}
