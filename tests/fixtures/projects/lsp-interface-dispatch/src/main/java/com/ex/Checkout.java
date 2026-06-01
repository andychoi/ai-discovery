package com.ex;
import org.springframework.beans.factory.annotation.Autowired;
public class Checkout {
    @Autowired private PaymentProcessor processor;
    public void run() {
        processor.process();
    }
}
