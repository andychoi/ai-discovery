package shop.pay;

public interface PaymentGateway {
    void charge(long cents);
}
